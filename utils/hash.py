import hashlib


def hash_str(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()