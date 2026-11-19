# output — Writing the result into the library: the verified in-place swap (D-0002). Exports: update_in_place, write_run, verdict_record, subtitle_streams, current_path, marked_name, marker, OutputResult, OutputError.

from .inplace import OutputError, OutputResult, marked_name, marker, subtitle_streams, update_in_place
from .run import current_path, verdict_record, write_run

__all__ = ["update_in_place", "write_run", "current_path", "marked_name", "marker", "subtitle_streams", "verdict_record", "OutputResult", "OutputError"]
