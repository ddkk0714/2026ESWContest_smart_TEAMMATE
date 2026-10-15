"""Frozen small CNN and trainable softmax head, using only board-safe stdlib."""
import json
import math

_SHA256_K = (
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
)


def pure_sha256_hex(data):
    """FIPS 180-4 SHA-256. The ATLAS restricted Python ships hashlib without sha256 (10-12 measured)."""
    mask = 0xffffffff
    h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
    message = bytes(data) + b"\x80" + b"\x00" * ((55 - len(data)) % 64) + (len(data) * 8).to_bytes(8, "big")
    for offset in range(0, len(message), 64):
        w = [int.from_bytes(message[offset + i:offset + i + 4], "big") for i in range(0, 64, 4)]
        for i in range(16, 64):
            x, y = w[i - 15], w[i - 2]
            s0 = ((x >> 7 | x << 25) ^ (x >> 18 | x << 14) ^ (x >> 3)) & mask
            s1 = ((y >> 17 | y << 15) ^ (y >> 19 | y << 13) ^ (y >> 10)) & mask
            w.append((w[i - 16] + s0 + w[i - 7] + s1) & mask)
        a, b, c, d, e, f, g, k = h
        for i in range(64):
            s1 = (e >> 6 | e << 26) ^ (e >> 11 | e << 21) ^ (e >> 25 | e << 7)
            t1 = (k + s1 + ((e & f) ^ (~e & g)) + _SHA256_K[i] + w[i]) & mask
            s0 = (a >> 2 | a << 30) ^ (a >> 13 | a << 19) ^ (a >> 22 | a << 10)
            t2 = (s0 + ((a & b) ^ (a & c) ^ (b & c))) & mask
            a, b, c, d, e, f, g, k = (t1 + t2) & mask, a, b, c, (d + t1) & mask, e, f, g
        h = [(old + new) & mask for old, new in zip(h, (a, b, c, d, e, f, g, k))]
    return "".join("%08x" % value for value in h)


def sha256_hex(data):
    try:
        from hashlib import sha256
    except ImportError:
        return pure_sha256_hex(data)
    return sha256(data).hexdigest()


def finite_tree(value, shape):
    if not shape:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid weight")
        return
    if not isinstance(value, list) or len(value) != shape[0]:
        raise ValueError("weight shape mismatch")
    for item in value:
        finite_tree(item, shape[1:])


def softmax(logits):
    offset = max(logits)
    values = [math.exp(x - offset) for x in logits]
    total = sum(values)
    return [x / total for x in values]


class Head:
    def __init__(self, weights, bias):
        finite_tree(weights, (16, 4))
        finite_tree(bias, (4,))
        self.weights = [list(row) for row in weights]
        self.bias = list(bias)

    def copy(self):
        return Head(self.weights, self.bias)

    def predict(self, embedding):
        finite_tree(embedding, (16,))
        return softmax([self.bias[j] + sum(x * row[j] for x, row in zip(embedding, self.weights))
                        for j in range(4)])

    def update(self, embedding, label, *, rate, l2):
        if type(label) is not int or not 0 <= label < 4:
            raise ValueError("invalid label")
        probabilities = self.predict(embedding)
        for j in range(4):
            error = probabilities[j] - int(j == label)
            self.bias[j] -= rate * error
            for i, x in enumerate(embedding):
                self.weights[i][j] -= rate * (x * error + l2 * self.weights[i][j])
        finite_tree(self.weights, (16, 4))
        finite_tree(self.bias, (4,))

    def record(self):
        return {"weights": self.weights, "bias": self.bias}


class PortableBackend:
    """Exact supported architecture: same Conv1D x2, mean pool, dense ReLU, head."""
    def __init__(self, path, metadata):
        with open(path, "rb") as source:
            content = source.read()
        self.fingerprint = sha256_hex(content)
        if metadata.get("model_sha256") != self.fingerprint:
            raise ValueError("portable fingerprint required")
        model = json.loads(content.decode("utf-8"))
        expected = {
                k: metadata[k] for k in ("contract_version", "features", "classes", "input_shape",
                                         "output_shape", "dtype", "frame_period_sec", "normalization")}
        if "feature_profile" in metadata:
            expected["feature_profile"] = metadata["feature_profile"]
        if model.get("format") != "deskmate-portable-cnn/1" or model.get("metadata") != expected:
            raise ValueError("portable contract mismatch")
        self.width = metadata["input_shape"][1]
        self.layers = model["layers"]
        for name, shape in (("conv1", (3, 17, 16)), ("conv2", (3, 16, 16)), ("embedding", (16, 16))):
            finite_tree(self.layers[name]["weights"], shape)
            finite_tree(self.layers[name]["bias"], (16,))
        self.common_head = Head(**model["head"])
        self.head = self.common_head.copy()
        self.last_embedding = None

    @staticmethod
    def _conv(rows, layer):
        weights, bias = layer["weights"], layer["bias"]
        outputs = []
        for t in range(len(rows)):
            values = list(bias)
            for k in range(3):
                index = t + k - 1
                if 0 <= index < len(rows):
                    for x, row in zip(rows[index], weights[k]):
                        for j in range(16):
                            values[j] += x * row[j]
            outputs.append([max(0.0, x) for x in values])
        return outputs

    def embed(self, window):
        finite_tree(window, (self.width, 17))
        rows = self._conv(self._conv(window, self.layers["conv1"]), self.layers["conv2"])
        pooled = [sum(row[i] for row in rows) / len(rows) for i in range(16)]
        layer = self.layers["embedding"]
        return [max(0.0, layer["bias"][j] + sum(x * row[j] for x, row in zip(pooled, layer["weights"])))
                for j in range(16)]

    def predict(self, window):
        self.last_embedding = self.embed(window)
        return self.head.predict(self.last_embedding)
