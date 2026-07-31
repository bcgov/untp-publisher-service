import requests, random
from config import settings
from bitstring import BitArray
import gzip, base64


class BitstringStatusListError(Exception):
    """Generic BitstringStatusList Error."""


class BitstringStatusList:
    def __init__(self):
        # self.store = AskarStorage()
        self.length = 200000

    def generate(self, bitstring):
        # https://www.w3.org/TR/vc-bitstring-status-list/#bitstring-generation-algorithm
        statusListBitarray = BitArray(bin=bitstring)
        statusListCompressed = gzip.compress(statusListBitarray.bytes)
        statusList_encoded = (
            base64.urlsafe_b64encode(statusListCompressed).decode("utf-8").rstrip("=")
        )
        return statusList_encoded

    def expand(self, encoded_list):
        # https://www.w3.org/TR/vc-bitstring-status-list/#bitstring-expansion-algorithm
        raw = (encoded_list or "").strip()
        # Spec Multibase base64url uses a leading ``u``; our create() path omits it.
        if raw.startswith("u"):
            raw = raw[1:]
        pad = "=" * ((4 - len(raw) % 4) % 4)
        statusListCompressed = base64.urlsafe_b64decode(raw + pad)
        statusListBytes = gzip.decompress(statusListCompressed)
        statusListBitarray = BitArray(bytes=statusListBytes)
        statusListBitstring = statusListBitarray.bin
        return statusListBitstring

    def set_status_bit(self, encoded_list: str, index: int, value: bool) -> str:
        """Return a new ``encodedList`` with ``index`` set to 1 (True) or 0 (False)."""
        bitstring = self.expand(encoded_list)
        bits = list(bitstring)
        if index < 0 or index >= len(bits):
            raise BitstringStatusListError(
                f"statusListIndex {index} out of range for list length {len(bits)}"
            )
        bits[index] = "1" if value else "0"
        return self.generate("".join(bits))

    async def create(self, id=None, issuer=None, purpose="revocation", length=200000):
        # https://www.w3.org/TR/vc-bitstring-status-list/#example-example-bitstringstatuslistcredential
        status_list_credential = {
            "@context": [
                "https://www.w3.org/ns/credentials/v2",
            ],
            "type": ["VerifiableCredential", "BitstringStatusListCredential"],
            "credentialSubject": {
                "type": "BitstringStatusList",
                "encodedList": self.generate(str(0) * length),
                "statusPurpose": purpose,
            },
        }
        if id:
            status_list_credential["id"] = id
        if issuer:
            status_list_credential["issuer"] = issuer

        return status_list_credential

    def get_credential_status(self, vc):
        # https://www.w3.org/TR/vc-bitstring-status-list/#validate-algorithm
        statusListIndex = int(vc["credentialStatus"]["statusListIndex"])
        statusListCredentialUri = vc["credentialStatus"]["statusListCredential"]

        r = requests.get(statusListCredentialUri)
        statusListCredential = r.json()
        statusListBitstring = self.expand(
            statusListCredential["credentialSubject"]["encodedList"]
        )
        statusList = list(statusListBitstring)
        credentialStatusBit = statusList[statusListIndex]
        return True if credentialStatusBit == "1" else False
