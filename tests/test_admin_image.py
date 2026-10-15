"""Regression coverage for the privileged database-bootstrap image boundary."""

from scripts.run_admin import is_pinned_admin_image


def test_admin_image_requires_exact_destination_registry_and_digest():
    account = "123456789012"
    region = "us-west-2"
    expected = f"{account}.dkr.ecr.{region}.amazonaws.com/ecc-challenges-admin@sha256:"
    digest = "a" * 64

    assert is_pinned_admin_image(expected + digest, account, region)
    assert not is_pinned_admin_image("evil.example/" + expected + digest, account, region)
    assert not is_pinned_admin_image(expected + digest + "/extra", account, region)
    assert not is_pinned_admin_image(expected + "a" * 63, account, region)
