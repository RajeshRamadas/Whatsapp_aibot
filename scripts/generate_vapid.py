"""Create a private environment file for Web Push configuration."""
import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--subject', required=True, help='Contact URI, e.g. mailto:ops@example.com')
    parser.add_argument('--output', default='.env.push')
    args = parser.parse_args()
    if not args.subject.startswith(('mailto:', 'https://')) or any(c in args.subject for c in '\r\n'):
        parser.error('subject must be a mailto: or HTTPS contact URI')
    private = ec.generate_private_key(ec.SECP256R1())
    private_der = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint)
    values = (
        'INBOX_VAPID_PRIVATE_KEY=' + base64.b64encode(private_der).decode() + '\n'
        'INBOX_VAPID_PUBLIC_KEY=' + base64.urlsafe_b64encode(public).decode().rstrip('=') + '\n'
        'INBOX_VAPID_SUBJECT=' + args.subject + '\n'
    )
    descriptor = os.open(Path(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(values)
    print(f'Created {args.output}. Store it securely and load its values into the API and worker environment.')


if __name__ == '__main__':
    main()
