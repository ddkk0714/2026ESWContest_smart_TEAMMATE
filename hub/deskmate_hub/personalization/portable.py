"""Frozen small CNN and trainable softmax head, using only board-safe stdlib."""
import json
import math


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
        # Fingerprint support is optional on ATLAS; its absence must not break FSM imports.
        import hashlib
        with open(path, "rb") as source:
            content = source.read()
        self.fingerprint = hashlib.sha256(content).hexdigest()
        if metadata.get("model_sha256") != self.fingerprint:
            raise ValueError("portable fingerprint required")
        model = json.loads(content.decode("utf-8"))
        if model.get("format") != "deskmate-portable-cnn/1" or model.get("metadata") != {
                k: metadata[k] for k in ("contract_version", "features", "classes", "input_shape",
                                         "output_shape", "dtype", "frame_period_sec", "normalization")}:
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
