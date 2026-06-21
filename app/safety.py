import re
import html

def sanitize_user_input(raw: str) -> str:
    """
    Escapes existing HTML/XML tags in the user input to prevent prompt injection,
    then wraps the sanitized input in <user_message> delimiters.
    """
    if not raw:
        return ""
    
    escaped_text = html.escape(raw)
    return f"<user_message>{escaped_text}</user_message>"

def mask_pii(text: str) -> str:
    """
    Masks 16-digit credit card numbers and 10-digit phone numbers.
    Credit cards are replaced with ****-****-****-XXXX.
    Phone numbers are replaced with PHONE_REDACTED.
    """
    if not text:
        return ""

    # Mask 16-digit credit cards (handles optional spaces or dashes)
    # Matches formats like: 1234567812345678, 1234-5678-1234-5678, 1234 5678 1234 5678
    def cc_replacer(match):
        raw_match = match.group(0)
        # Strip spaces/dashes to easily grab the last 4 digits
        digits_only = re.sub(r'[\s-]', '', raw_match)
        last_four = digits_only[-4:]
        return f"****-****-****-{last_four}"

    cc_pattern = r'\b(?:\d{4}[\s-]?){3}\d{4}\b'
    text = re.sub(cc_pattern, cc_replacer, text)

    # Mask 10-digit phone numbers (handles optional spaces or dashes)
    # Matches formats like: 1234567890, 123-456-7890, 123 456 7890
    phone_pattern = r'\b\d{3}[\s-]?\d{3}[\s-]?\d{4}\b'
    text = re.sub(phone_pattern, "PHONE_REDACTED", text)

    return text