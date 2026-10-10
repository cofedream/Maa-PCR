"""Small stdout contract for the owner's human captcha browser handoff.

Only the ephemeral public loopback page address is sent. This is not a general
logging or browser-command channel; credentials and captcha proofs are excluded.
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
