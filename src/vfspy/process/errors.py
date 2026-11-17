class BridgeProcessError(RuntimeError):
    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        message = f"native bridge exited with status {returncode}"
        if stderr:
            message += f"\n{stderr[-4096:].rstrip()}"
        super().__init__(message)
