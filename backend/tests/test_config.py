from document_enrichment.config import Settings


def test_default_upload_limits_are_safe() -> None:
    settings = Settings()
    assert settings.max_upload_bytes == 256 * 1024
    assert settings.max_document_characters == 30_000
