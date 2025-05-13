import time
from backend.app import InMemoryTTLCache

def test_basic_set_get():
    cache = InMemoryTTLCache()
    cache.set("abc", 123)
    assert cache.get("abc") == 123       # recupera mismo valor
