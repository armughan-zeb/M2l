from unittest.mock import patch
import subprocess
from services import media_integrity as integrity


def test_header_valid_but_corrupt_video_is_not_promoted(tmp_path):
    staged=tmp_path/'clip.part.mp4';staged.write_bytes(b'corrupt')
    final=tmp_path/'clip.mp4'
    probe=integrity.MediaProbeResult(str(staged),True,duration=3,width=1920,height=1080,video_streams=1)
    with patch.object(integrity,'probe_media',return_value=probe), patch.object(integrity.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','Invalid OBU length')) as run:
        result=integrity.promote_verified_media(staged,final,require_decode=True,ffmpeg_path='ffmpeg')
    assert not result.valid and result.reason=='video_decode_failed'
    assert staged.exists() and not final.exists()
    assert '-xerror' in run.call_args.args[0]


def test_decode_timeout_keeps_retryable_source_and_no_final(tmp_path):
    staged=tmp_path/'clip.part.mp4';staged.write_bytes(b'corrupt')
    probe=integrity.MediaProbeResult(str(staged),True,duration=3,video_streams=1)
    with patch.object(integrity,'probe_media',return_value=probe), patch.object(integrity.subprocess,'run',side_effect=subprocess.TimeoutExpired('ffmpeg',10)):
        result=integrity.promote_verified_media(staged,tmp_path/'clip.mp4',require_decode=True,ffmpeg_path='ffmpeg')
    assert result.reason=='video_decode_timeout'
    assert staged.exists() and not (tmp_path/'clip.mp4').exists()
