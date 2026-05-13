import base64

from django.core.management.base import BaseCommand


def _urlsafe_base64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Generate VAPID keys for PWA Web Push notifications."

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError as error:
            raise SystemExit("Install requirements first so cryptography is available.") from error

        private_key = ec.generate_private_key(ec.SECP256R1())
        private_number = private_key.private_numbers().private_value
        private_bytes = private_number.to_bytes(32, "big")
        public_numbers = private_key.public_key().public_numbers()
        public_bytes = (
            b"\x04"
            + public_numbers.x.to_bytes(32, "big")
            + public_numbers.y.to_bytes(32, "big")
        )

        self.stdout.write("Add these environment variables in production:")
        self.stdout.write(f"PWA_VAPID_PUBLIC_KEY={_urlsafe_base64(public_bytes)}")
        self.stdout.write(f"PWA_VAPID_PRIVATE_KEY={_urlsafe_base64(private_bytes)}")
        self.stdout.write("PWA_VAPID_SUBJECT=mailto:your-email@example.com")
