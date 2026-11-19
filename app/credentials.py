"""Explicit native credential backends; unavailable platforms fail closed."""
import sys


class NativeVault:
    # Keep the service and keys stable for existing Windows credentials.
    service = 'ViceScopeClipStudio.YouTube'

    def __init__(self, platform=None):
        platform = sys.platform if platform is None else platform
        if platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring

            self.backend = WinVaultKeyring()
            self.backend.persist = 'local machine'
        elif platform == 'darwin':
            from keyring.backends.macOS import Keyring

            self.backend = Keyring()
        else:
            raise RuntimeError('No supported native credential store on this platform')

    def get(self, key):
        return self.backend.get_password(self.service, key)

    def put(self, key, value):
        self.backend.set_password(self.service, key, value)

    def delete(self, key):
        if self.get(key) is not None:
            self.backend.delete_password(self.service, key)
