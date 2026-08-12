"""One-time Strava authorization. Run this once:  python strava_auth.py

It prints an authorization URL, you approve access in the browser, then paste
the redirected URL back here. We exchange it for tokens and store them in
.strava_tokens.json (git-ignored). After this, the app refreshes tokens on its
own — you never repeat this step unless you revoke access.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import strava


def _extract_code(pasted: str) -> str:
    """Accept either the full redirected URL or a bare code."""
    pasted = pasted.strip()
    if pasted.startswith("http"):
        code = parse_qs(urlparse(pasted).query).get("code", [""])[0]
        if not code:
            raise SystemExit("No ?code= found in that URL. Copy the full address bar URL.")
        return code
    return pasted


def main() -> None:
    print("STEP 1 — Open this URL in your browser and click Authorize:\n")
    print("  " + strava.authorize_url() + "\n")
    print("STEP 2 — Strava redirects to a 'localhost' page that WON'T load.")
    print("         That's expected. Copy the full URL from the address bar —")
    print("         it looks like  http://localhost/exchange_token?state=&code=ABC123&scope=...\n")

    pasted = input("Paste the redirected URL (or just the code): ")
    code = _extract_code(pasted)

    tokens = strava.exchange_code(code)
    athlete = tokens.get("athlete", {})
    name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
    scope_ok = "activity:read" in " ".join(
        s for s in [tokens.get("scope", "")]  # scope isn't always echoed here
    )

    print(f"\n✓ Authorized{f' as {name}' if name else ''}. Tokens saved to .strava_tokens.json")
    print("You can now run:  python strava.py   (lists your latest activities)")
    if not scope_ok and not name:
        print(
            "\nNote: if the activity list comes back empty, re-run this and make sure "
            "the 'View data about your activities' box stays checked on the approval screen."
        )


if __name__ == "__main__":
    main()
