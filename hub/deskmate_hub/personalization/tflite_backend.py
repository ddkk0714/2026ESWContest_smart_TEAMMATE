"""Loaded only on explicit enablement, after metadata validation."""


class TFLiteBackend:
    def __init__(self, path, metadata, *, interpreter_factory=None):
        import numpy as np
        if metadata.get("model_sha256"):
            import hashlib
            with open(path, "rb") as source:
                if hashlib.sha256(source.read()).hexdigest() != metadata["model_sha256"]:
                    raise ValueError("model fingerprint mismatch")
        if interpreter_factory is None:
            from tflite_runtime.interpreter import Interpreter
            interpreter_factory = Interpreter
        self.np = np
        self.interpreter = interpreter_factory(model_path=path, num_threads=1)
        self.interpreter.allocate_tensors()
        inputs = self.interpreter.get_input_details()
        outputs = self.interpreter.get_output_details()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("one input/output required")
        self.input, self.output = inputs[0], outputs[0]
        for detail, shape in ((self.input, metadata["input_shape"]), (self.output, metadata["output_shape"])):
            if list(detail["shape"]) != shape or detail["dtype"] != np.float32:
                raise ValueError("tensor contract mismatch")

    def predict(self, window):
        tensor = self.np.asarray([window], dtype=self.np.float32)
        self.interpreter.set_tensor(self.input["index"], tensor)
        self.interpreter.invoke()
        return self.interpreter.get_tensor(self.output["index"])[0].tolist()
