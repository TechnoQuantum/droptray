import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import multipart_events, filename_from, safe_name

def body(boundary, parts):
    out = b"preamble junk\r\n"
    for headers, data in parts:
        out += b"--" + boundary + b"\r\n" + headers + b"\r\n\r\n" + data + b"\r\n"
    return out + b"--" + boundary + b"--\r\n"

def chunked(b, sizes):
    i = 0
    while i < len(b):
        n = random.choice(sizes); yield b[i:i+n]; i += n

def run(chunk_sizes):
    bd = b"----WebKitFormBoundaryXyZ123"
    payload = os.urandom(300_000) + b"\r\n--" + bd[:-1] + os.urandom(1000)  # near-miss boundary inside data
    raw = body(bd, [(b'Content-Disposition: form-data; name="note"', b"hello"),
                    (b'Content-Disposition: form-data; name="file"; filename="IMG_0001.HEIC"\r\nContent-Type: image/heic', payload)])
    events = list(multipart_events(chunked(raw, chunk_sizes), bd))
    starts = [v for k, v in events if k == "start"]
    assert len(starts) == 2, starts
    assert filename_from(starts[1]) == "IMG_0001.HEIC"
    # collect data of the second part
    got, part = b"", -1
    for k, v in events:
        if k == "start": part += 1
        elif k == "data" and part == 1: got += v
    assert got == payload, (len(got), len(payload))

for sizes in ([1, 2, 3, 7], [64, 100, 999], [262144], [5, 70000]):
    run(sizes)
assert filename_from("form-data; filename*=UTF-8''f%C3%B6to.jpg") == "föto.jpg"
assert safe_name("../../evil\CON.txt") == "_CON.txt"
assert safe_name('a<b>:c?.jpg') == "a_b__c_.jpg"
print("multipart + filename tests passed")
