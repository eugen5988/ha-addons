"""Nachbau der pytr-API fuer Tests (kein Netz)."""
import asyncio, os, time
ZUSTAND = {"angemeldet": set(), "logins": [], "pin_falsch": False, "bestaetigung_s": 1.5}
ITEMS = [
    {"id": "a1", "eventType": "CARD_TRANSACTION", "timestamp": "2026-11-20T10:15:00.000+0000", "title": "REWE Markt",
     "amount": {"value": -23.45, "currency": "EUR"}, "status": "EXECUTED"},
    {"id": "a2", "eventType": "CARD_VERIFICATION", "timestamp": "2026-11-20T09:00:00.000+0000", "title": "Netflix",
     "amount": {"value": 0, "currency": "EUR"}, "status": "EXECUTED"},
    {"id": "a3", "eventType": "CARD_TRANSACTION", "timestamp": "2026-11-21T18:02:00.000+0000", "title": "Shell Station",
     "amount": {"value": -61.10, "currency": "EUR"}, "status": "EXECUTED"},
]
class TradeRepublicApi:
    def __init__(self, phone_no=None, pin=None, save_cookies=False, use_v2_login=False, **kw):
        if not (phone_no and pin):
            raise ValueError("phone_no and pin must be specified")
        self.phone_no, self.pin = phone_no, pin
        self.weblogin_needs_authenticator = False
        self._q = []
    def resume_websession(self):
        return self.phone_no in ZUSTAND["angemeldet"]
    def initiate_weblogin(self):
        ZUSTAND["logins"].append((self.phone_no, self.pin))
        if ZUSTAND["pin_falsch"] or self.pin == "0000":
            raise Exception("401 Client Error: Unauthorized - INVALID PIN")
    def complete_weblogin(self, code=None):
        time.sleep(ZUSTAND["bestaetigung_s"])
        ZUSTAND["angemeldet"].add(self.phone_no)
        d = os.path.join(os.environ["HOME"], ".pytr"); os.makedirs(d, exist_ok=True)
        open(os.path.join(d, f"cookies.{self.phone_no}.txt"), "w").write("# cookies\n")
    async def timeline_transactions(self, after=None):
        self._q.append((1, {"type": "timelineTransactions"}, {"items": ITEMS, "cursors": {}}))
        return 1
    async def recv(self):
        return self._q.pop(0)
    async def unsubscribe(self, sid):
        pass
    async def close(self):
        pass
