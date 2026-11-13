"""Drum notation generation and export logic."""


class NotationExportError(RuntimeError):
	"""A transcription or external renderer could not produce an export."""