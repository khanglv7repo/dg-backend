#!/usr/bin/env python3
import os
import sys
from urllib.parse import quote

import httpx

try:
    from app.core.config import get_settings
except Exception as exc:
    print("ERROR: run this script from governance_app so `app` can be imported.")
    print(f"DETAIL: {exc}")
    sys.exit(2)

TAGS = [
    ("PII", "Address", "Postal or physical address information."),
    ("PII", "CustomerIdentifier", "Identifier associated with a customer."),
    ("PII", "DateOfBirth", "Person date of birth."),
    ("PII", "EmployeeName", "Employee or staff member name."),
    ("PII", "Name", "Person name."),
    ("PII", "Preference", "Customer or person preference information."),
    ("Sensitivity", "Internal", "Information intended for internal use."),
]

def main() -> int:
    admin_token ="eyJraWQiOiJsb2NhbC1kZXYta2V5IiwiYWxnIjoiUlMyNTYiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJvcGVuLW1ldGFkYXRhLm9yZyIsInN1YiI6ImF1dG9jbGFzc2lmaWNhdGlvbi1ib3QiLCJyb2xlcyI6WyJBdXRvQ2xhc3NpZmljYXRpb25Cb3RSb2xlIl0sImVtYWlsIjoiYXV0b2NsYXNzaWZpY2F0aW9uLWJvdEBvcGVuLW1ldGFkYXRhLm9yZyIsImlzQm90Ijp0cnVlLCJ0b2tlblR5cGUiOiJCT1QiLCJ1c2VybmFtZSI6ImF1dG9jbGFzc2lmaWNhdGlvbi1ib3QiLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJhdXRvY2xhc3NpZmljYXRpb24tYm90IiwiaWF0IjoxNzg1NTU2NjExLCJleHAiOm51bGx9.Ba9vhQPEugQXIEBquZO0H2z-no1A1gmtU4A9S5XQjafMOZjSvaQEZxhBf71WZzaJYvcTKYJtFlTQZui7ZnfBG1q2ctJKj4RjLuPYll6O24p4zcxn9RhtpKmZyXUt_LKIMlmW1_pCRYjf0Vrb0nZDrcmdQRs-GnbsUVRS-mntQceXNVEcGZ2m1ORfcMUpKTLYJX4nQs-SxCNqv-98b8fEY8wEkEfOlFGvqIpWmSVKEMQaKUQEFEwxBtQDO7pimRYKaNiIKzUQfYIr3eeSKJPsiXBKB1gLka56miuwEKckTVNzXVWb61APJ_hNFVPopp-sB03_TtvNWJdX7Z_fzkR31Q"

    settings = get_settings()
    base_url = settings.openmetadata_base_url.rstrip("/")

    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    created = 0
    skipped = 0
    failed = 0

    print(f"OpenMetadata: {base_url}\n")

    with httpx.Client(base_url=base_url, headers=headers, timeout=20) as client:
        for classification, name, description in TAGS:
            tag_fqn = f"{classification}.{name}"
            encoded = quote(tag_fqn, safe="")

            check = client.get(f"/v1/tags/name/{encoded}")

            if check.status_code == 200:
                print(f"SKIP   {tag_fqn} (already exists)")
                skipped += 1
                continue

            if check.status_code != 404:
                print(f"FAIL   {tag_fqn} (check {check.status_code}: {check.text[:300]})")
                failed += 1
                continue

            payload = {
                "classification": classification,
                "name": name,
                "displayName": name,
                "description": description,
            }

            response = client.post("/v1/tags", json=payload)

            if response.status_code in (200, 201):
                print(f"CREATE {tag_fqn} ({response.status_code})")
                created += 1
            elif response.status_code == 409:
                print(f"SKIP   {tag_fqn} (already exists / conflict)")
                skipped += 1
            else:
                print(f"FAIL   {tag_fqn} ({response.status_code}: {response.text[:500]})")
                failed += 1

        print("\nVERIFY")
        print("-" * 60)

        for classification, name, _ in TAGS:
            tag_fqn = f"{classification}.{name}"
            encoded = quote(tag_fqn, safe="")
            response = client.get(f"/v1/tags/name/{encoded}")
            print(f"{response.status_code} {tag_fqn}")

    print(f"\ncreated={created} skipped={skipped} failed={failed}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
