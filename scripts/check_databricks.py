"""Read-only CLI authentication and SQL warehouse discovery; never print credentials."""
import argparse
import json
import shutil
import subprocess
import sys
import time


def sql_smoke_test(warehouse_id, profile=None, timeout=150):
    payload = {"warehouse_id": warehouse_id, "statement": "SELECT 1 AS connection_ok",
               "wait_timeout": "0s", "disposition": "INLINE", "format": "JSON_ARRAY"}
    response, error = check(["api", "post", "/api/2.0/sql/statements", "--json", json.dumps(payload)], profile)
    if error:
        print("SQL execution UNVERIFIED:", error)
        return 3
    statement_id = response.get("statement_id")
    deadline = time.monotonic() + timeout
    while True:
        state = response.get("status", {}).get("state")
        if state == "SUCCEEDED":
            values = response.get("result", {}).get("data_array")
            if values not in ([["1"]], [[1]]):
                print("SQL execution UNVERIFIED: expected a single result equal to 1.")
                return 3
            print("SQL execution VERIFIED: SELECT 1 returned 1.")
            print("Statement ID:", statement_id)
            print("No tables created or modified. Warehouse startup/compute may have been used.")
            return 0
        if state not in {"PENDING", "RUNNING"} or not statement_id:
            print("SQL execution UNVERIFIED; statement state:", state)
            return 3
        if time.monotonic() >= deadline:
            _, cancel_error = check(["api", "post", f"/api/2.0/sql/statements/{statement_id}/cancel"], profile)
            print("SQL execution UNVERIFIED: startup/execution timed out.")
            print("Cancellation:", cancel_error or "requested")
            return 3
        time.sleep(2)
        response, error = check(["api", "get", f"/api/2.0/sql/statements/{statement_id}"], profile)
        if error:
            print("SQL status UNVERIFIED:", error)
            print("Statement ID:", statement_id)
            return 3


def check(arguments, profile=None):
    command = ["databricks", *arguments, "--output", "json"]
    if profile:
        command.extend(["--profile", profile])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return None, "Request timed out after 25 seconds."
    if result.returncode:
        error = result.stderr.lower()
        if "no cached credentials" in error or "login required" in error:
            reason = "No cached login credentials accessible. Run databricks auth login in your terminal."
        elif any(text in error for text in ("no such host", "could not resolve", "network is unreachable", "dial tcp", "operation not permitted")):
            reason = "Network or execution restrictions prevented the request."
        elif "401" in error or "unauthorized" in error:
            reason = "Authentication rejected; refresh your Databricks login."
        elif "403" in error or "forbidden" in error or "permission denied" in error:
            reason = "Access denied; verify workspace permissions."
        else:
            reason = "CLI request failed; run the equivalent command locally for details."
        return None, reason
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, "CLI response was not valid JSON."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="Optional CLI profile; default uses your existing configuration.")
    parser.add_argument("--sql", action="store_true", help="Execute SELECT 1; may start the selected SQL warehouse and use compute.")
    parser.add_argument("--warehouse-id", help="Warehouse to test; defaults to the only visible warehouse.")
    args = parser.parse_args()
    if not shutil.which("databricks"):
        print("Databricks CLI is not installed.")
        return 1
    identity, error = check(["current-user", "me"], args.profile)
    if error:
        print("Authentication UNVERIFIED:", error)
        print("No credentials or raw service responses printed.")
        return 1
    if not isinstance(identity, dict) or not (identity.get("id") or identity.get("userName")):
        print("Authentication UNVERIFIED: response did not contain a user identity.")
        return 1
    print("Workspace authentication VERIFIED (identity details hidden).")
    warehouses, error = check(["warehouses", "list"], args.profile)
    if error:
        print("SQL warehouse discovery UNVERIFIED:", error)
        return 2
    if isinstance(warehouses, dict):
        warehouses = warehouses.get("warehouses", [])
    if not isinstance(warehouses, list):
        print("Unexpected warehouse response; SQL availability UNVERIFIED.")
        return 2
    print("SQL warehouses visible:", len(warehouses))
    for warehouse in warehouses:
        print(json.dumps({key: warehouse.get(key) for key in ("id", "state", "warehouse_type", "enable_serverless_compute")}))
    if not warehouses:
        print("Authentication works, but no SQL warehouse is visible to this user.")
        return 2
    if args.sql:
        warehouse_id = args.warehouse_id or (warehouses[0].get("id") if len(warehouses) == 1 else None)
        if not warehouse_id or not any(w.get("id") == warehouse_id for w in warehouses):
            print("Choose a visible warehouse using --warehouse-id.")
            return 2
        print("Running SELECT 1 on the selected warehouse; this may start compute.")
        return sql_smoke_test(warehouse_id, args.profile)
    print("Read-only discovery complete. No warehouse started, SQL executed, or data modified.")
    print("Next: select a warehouse and verify SQL execution separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
