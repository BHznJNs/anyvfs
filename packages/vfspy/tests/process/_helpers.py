from vfspy.process import BridgeProcessConfig


def config(**overrides):
    return BridgeProcessConfig(
        **{
            "mountpoint": "Z:",
            "endpoint": "127.0.0.1:12345",
            "token": "child-secret",
            **overrides,
        }
    )
