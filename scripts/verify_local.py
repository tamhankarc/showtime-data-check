"""End-to-end local HTTP verification without printing credentials or session data.

--refresh explicitly calls authorized upstream providers for all enabled catalogs.
No showtime bodies, passwords, cookies, URLs with secrets, or exports are logged.
"""
import argparse
import json
from pathlib import Path
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]


def credentials(role):
    lines = (ROOT / "private/local-access.txt").read_text(encoding="utf-8").splitlines()
    prefix = "Full access" if role == "admin" else "Refresh/view"
    return {"username": next(line.split(": ", 1)[1] for line in lines if line.startswith(prefix + " username:")),
            "password": next(line.split(": ", 1)[1] for line in lines if line.startswith(prefix + " password:"))}


def wait(client, identifier):
    previous = None
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{identifier}")
        response.raise_for_status()
        job = response.json()
        update = {"status": job["status"], "progress": job["progress"], "outcomes": [{"provider": item["provider_code"], "country": item.get("country_code"), "status": item["status"]} for item in job["provider_outcomes"]]}
        if update != previous:
            print(json.dumps(update), flush=True)
            previous = update
        if job["status"] not in {"queued", "processing"}:
            return job
        time.sleep(3)
    raise RuntimeError("Verification timed out waiting for the worker")


def main(refresh=False, live_check=False, refresh_velox=False, br_check=False, br_movie="Practical Magic 2"):
    with httpx.Client(base_url="http://localhost:3001", headers={"Origin": "http://localhost:3001"}, timeout=55, follow_redirects=True) as client:
        response = client.post("/api/login", json=credentials("admin"))
        response.raise_for_status()
        bootstrap = client.get("/api/bootstrap").json()
        movies = client.get("/api/provider-movies", params={"country": "BR", "page_size": 2}).json()
        print(json.dumps({"login": "admin_ok", "countries": len(bootstrap["countries"]), "connections": len(bootstrap["connections"]), "BR_movie_count": movies["total"], "page_size": len(movies["items"])}), flush=True)
        if refresh:
            response = client.post("/api/catalogs/refresh", json={"connection_ids": []})
            response.raise_for_status()
            job = wait(client, response.json()["job_id"])
            print(json.dumps({"catalog_verification": job["status"]}), flush=True)
        if refresh_velox:
            selected = [row["id"] for row in bootstrap["connections"] if row["country_code"] == "BR" and row["provider_code"] == "VELOX"]
            response = client.post("/api/catalogs/refresh", json={"connection_ids": selected})
            response.raise_for_status()
            job = wait(client, response.json()["job_id"])
            print(json.dumps({"velox_catalog": job["status"]}), flush=True)
        if br_check:
            groups = client.get("/api/movie-groups", params={"country": "BR", "q": br_movie, "page_size": 20}).json()["items"]
            group = next((row for row in groups if row["name"].casefold() == br_movie.casefold()), groups[0] if groups else None)
            if not group:
                raise RuntimeError("No currently available group for the selected Brazil live test movie")
            chosen = group["provider_ids"][:100]
            response = client.post("/api/checks", json={"country_code": "BR", "provider_movie_ids": [row["id"] for row in chosen]})
            response.raise_for_status()
            identifier = response.json()["job_id"]
            wait(client, identifier)
            result = client.get(f"/api/checks/{identifier}")
            result.raise_for_status()
            export = client.get(f"/api/checks/{identifier}/export?view=showtimes")
            export.raise_for_status()
            print(json.dumps({"BR_check": result.json()["status"], "providers": sorted({row["provider_code"] for row in chosen}), "selected_ids": len(chosen), "metrics": result.json()["metrics"], "xlsx_bytes": len(export.content)}), flush=True)
            client.delete(f"/api/checks/{identifier}")
        if live_check:
            choices = client.get("/api/provider-movies", params={"country": "AU", "q": "", "page_size": 1}).json()["items"]
            if choices:
                response = client.post("/api/checks", json={"country_code": "AU", "provider_movie_ids": [choices[0]["id"]]})
                response.raise_for_status()
                identifier = response.json()["job_id"]
                wait(client, identifier)
                result = client.get(f"/api/checks/{identifier}")
                result.raise_for_status()
                export = client.get(f"/api/checks/{identifier}/export?view=theaters")
                export.raise_for_status()
                print(json.dumps({"live_check": result.json()["status"], "metrics": result.json()["metrics"], "xlsx_bytes": len(export.content)}), flush=True)
                client.delete(f"/api/checks/{identifier}")
        client.post("/api/logout").raise_for_status()
        client.post("/api/login", json=credentials("viewer")).raise_for_status()
        assert client.patch("/api/theaters/1", json={"name": "not-applied"}).status_code == 403
        assert client.get("/api/provider-movies?country=BR&page_size=1").status_code == 200
        print(json.dumps({"viewer": "read_ok", "reference_write": "denied"}), flush=True)
        client.post("/api/logout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--live-check", action="store_true")
    parser.add_argument("--refresh-velox", action="store_true")
    parser.add_argument("--br-check", action="store_true")
    parser.add_argument("--br-movie", default="Practical Magic 2")
    args = parser.parse_args()
    main(args.refresh, args.live_check, args.refresh_velox, args.br_check, args.br_movie)
