"""Optional Telegram approvals: inline Approve / Block buttons, long polling in a background thread.

Only active when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Decisions are accepted only from that chat.
The token is part of every Bot API URL, so errors are reported by type only, never with the URL.
"""
import threading

import httpx


class Telegram:
    def __init__(self, token: str, chat_id: str, on_decision=None):
        self.token, self.chat_id, self.on_decision = token, str(chat_id or ""), on_decision
        self.enabled = bool(token and chat_id)
        self._messages: dict[str, int] = {}  # approval id -> telegram message id
        self._stop = threading.Event()

    def _call(self, method: str, http_timeout: float = 15, **body):
        r = httpx.post(f"https://api.telegram.org/bot{self.token}/{method}", json=body, timeout=http_timeout)
        r.raise_for_status()
        return r.json().get("result")

    @staticmethod
    def message_text(a: dict) -> str:
        reasons = "\n".join(f"- {r['text']}" for r in a.get("reasons", [])) or "-"
        return (f"Approval needed ({a.get('agent_name', 'agent')})\n\n{a['summary']}\n\nWhy you are asked:\n{reasons}\n\n"
                f"If nobody answers by {a['expires'][:16].replace('T', ' ')} UTC, it is blocked.")

    def send_approval(self, a: dict) -> bool:
        try:
            msg = self._call("sendMessage", chat_id=self.chat_id, text=self.message_text(a), reply_markup={
                "inline_keyboard": [[{"text": "Approve", "callback_data": f"ap:{a['id']}:approve"},
                                     {"text": "Block", "callback_data": f"ap:{a['id']}:block"}]]})
            self._messages[a["id"]] = msg["message_id"]
            return True
        except Exception as exc:
            print(f"Telegram send failed ({type(exc).__name__})")
            return False

    def mark_decided(self, a: dict):
        mid = self._messages.pop(a["id"], None)
        if mid:
            self._call("editMessageText", chat_id=self.chat_id, message_id=mid,
                       text=f"{a['summary']}\n\n{a['status'].upper()} by {a['decided_by']}")

    def handle_update(self, update: dict) -> str:
        """Process one Bot API update. Returns what happened (used by tests)."""
        cq = update.get("callback_query")
        if not cq:
            return "ignored: not a button press"
        chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat != self.chat_id:
            self._answer(cq, "This chat is not allowed to decide.")
            return "ignored: wrong chat"
        parts = (cq.get("data") or "").split(":")
        if len(parts) != 3 or parts[0] != "ap" or parts[2] not in ("approve", "block"):
            return "ignored: unknown button"
        who = cq.get("from") or {}
        name = who.get("username") or who.get("first_name") or str(who.get("id", "someone"))
        approval, status = self.on_decision(parts[1], parts[2] == "approve", f"{name} (Telegram)")
        self._answer(cq, "Recorded." if status == "ok" else f"Not changed: {status}.")
        if approval and status == "ok":
            self._messages[approval["id"]] = (cq.get("message") or {}).get("message_id")
            try:
                self.mark_decided(approval)
            except Exception:
                pass
        return status

    def _answer(self, cq, text):
        try:
            self._call("answerCallbackQuery", callback_query_id=cq.get("id"), text=text)
        except Exception:
            pass

    def start(self):
        if self.enabled:
            threading.Thread(target=self._loop, daemon=True, name="telegram-poll").start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        offset = None
        while not self._stop.is_set():
            try:
                updates = self._call("getUpdates", http_timeout=35, offset=offset, timeout=25,
                                     allowed_updates=["callback_query"]) or []
                for u in updates:
                    offset = u["update_id"] + 1
                    self.handle_update(u)
            except Exception as exc:
                print(f"Telegram polling error ({type(exc).__name__}), retrying in 5 s")
                self._stop.wait(5)
