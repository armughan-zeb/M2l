"""
Image Treatment Library — Ken Burns, pans, zooms, parallax via FFmpeg zoompan.

Each treatment returns a zoompan filter snippet.
"""

TREATMENTS = {}


def treatment(name: str):
    def deco(fn):
        TREATMENTS[name] = fn
        return fn
    return deco


@treatment("static_hold")
def static_hold(width=1920, height=1080, fps=30):
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"

@treatment("slow_push_in")
def slow_push_in(zoom=1.08, width=1920, height=1080, fps=30):
    return f"zoompan=z='min(zoom+0.0005\\,{zoom})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"

@treatment("slow_pull_out")
def slow_pull_out(zoom=1.12, width=1920, height=1080, fps=30):
    return f"zoompan=z='if(eq(in\\,0)\\,{zoom}\\,max(1.0\\,zoom-0.0005))':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"

@treatment("pan_left")
def pan_left(width=1920, height=1080, fps=30):
    return f"zoompan=z='1.05':x='iw-iw/zoom-1*t':d=1:s={width}x{height}:fps={fps}"

@treatment("pan_right")
def pan_right(width=1920, height=1080, fps=30):
    return f"zoompan=z='1.05':x='1*t':d=1:s={width}x{height}:fps={fps}"

@treatment("pan_up")
def pan_up(width=1920, height=1080, fps=30):
    return f"zoompan=z='1.05':y='ih-ih/zoom-1*t':d=1:s={width}x{height}:fps={fps}"

@treatment("pan_down")
def pan_down(width=1920, height=1080, fps=30):
    return f"zoompan=z='1.05':y='1*t':d=1:s={width}x{height}:fps={fps}"

@treatment("tilt_up")
def tilt_up(zoom=1.08, width=1920, height=1080, fps=30):
    return f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih-ih/zoom-1.5*t':d=1:s={width}x{height}:fps={fps}"

@treatment("tilt_down")
def tilt_down(zoom=1.08, width=1920, height=1080, fps=30):
    return f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='1.5*t':d=1:s={width}x{height}:fps={fps}"

@treatment("diagonal_pan")
def diagonal_pan(width=1920, height=1080, fps=30):
    return f"zoompan=z='1.06':x='iw-iw/zoom-0.8*t':y='ih-ih/zoom-0.5*t':d=1:s={width}x{height}:fps={fps}"

@treatment("parallax_push")
def parallax_push(zoom=1.12, width=1920, height=1080, fps=30):
    return f"zoompan=z='min(zoom+0.0008\\,{zoom})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)+0.3*t':s={width}x{height}:fps={fps}"

@treatment("drift")
def drift(width=1920, height=1080, fps=30):
    return f"zoompan=z='1.02+0.005*sin(t*0.3)':x='iw/2-(iw/zoom/2)+1.5*t':y='ih/2-(ih/zoom/2)+0.8*cos(t*0.4)':d=1:s={width}x{height}:fps={fps}"
