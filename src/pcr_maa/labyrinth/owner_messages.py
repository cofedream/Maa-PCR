"""The owner's stdout contract: one JSON object per line, nothing else.

Four kinds exist. `progress` reports a coarse stage, `captcha` carries only the
ephemeral public loopback page address of the human handoff, `result` is the
terminal receipt, and `log` is a display-only trace of confirmed steps. `log`
is not evidence and not a command channel: the GUI may render it and may drop
it, and its delivery never changes a result or a settled ledger entry. No
credentials, captcha proofs, exception text or URLs are ever put into `log`.
"""
import json
import re
import sys


class CaptchaMessageError(ValueError):
    pass


_PAGE_URL = re.compile(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})/[A-Za-z0-9_-]{43}/', re.ASCII)


def captcha_message(url, stage):
    """Validate the exact message accepted by the GUI owner."""
    if type(stage) is not str or stage not in ('sdk', 'game') or type(url) is not str:
        raise CaptchaMessageError('captcha_owner_message_invalid')
    matched = _PAGE_URL.fullmatch(url)
    if matched is None or not 1 <= int(matched.group(1)) <= 65535:
        raise CaptchaMessageError('captcha_owner_message_invalid')
    return dict(kind='captcha', stage=stage, url=url)


def emit_captcha_message(url, stage, *, stream=None):
    """HumanCaptcha.on_ready callback, with (url, stage) and immediate delivery."""
    message = captcha_message(url, stage)
    output = sys.stdout if stream is None else stream
    try:
        output.write(json.dumps(message, ensure_ascii=True, separators=(',', ':')) + '\n')
        output.flush()
    except Exception:
        # Pipe errors must not include the page nonce or arbitrary stream text.
        raise CaptchaMessageError('captcha_owner_delivery_failed') from None
