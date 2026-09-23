"""Make a 180-second narrated walkthrough from captured application states.

This is an edited screenshot walkthrough, not a continuous real-time recording.
Requires macOS 'say' and the local imageio-ffmpeg package. No external service.
"""
from pathlib import Path
import argparse
import json
import re
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageOps
import imageio_ffmpeg

ROOT=Path(__file__).resolve().parents[1]
SOURCES=ROOT/"options_lab_runs/demo"
OUT=ROOT/"options_lab_runs/deliverables"
WORK=ROOT/"options_lab_runs/demo_build"

SEGMENTS=[
 ("01_onboarding.jpg",20,"Start with identified inputs","Actual application captures. Engineering fixtures, not market data.",
  "This is the W T I Options Desk, a local research application built alongside my Cushing inventory study. This demonstration uses clearly labeled engineering fixtures. It demonstrates working software and model mechanics, not live market prices, real trading performance, or a completed historical study."),
 ("02_market.jpg",20,"Market, Draft, Active","CSO normal volatility and vanilla lognormal volatility remain separate.",
  "The market snapshot identifies the futures months, option terms, source and observation time. Calendar spread options use normal volatility. European vanilla options use annualized lognormal volatility. Market preserves the calibration, Draft holds edits, and Active is the version currently driving all calculations."),
 ("03_draft.jpg",25,"Stage a volatility change","Raise the CSO slice from 3.50 to 3.75. Active stays unchanged.",
  "Here I increase the C S O normal volatility slice from three point five to three point seven five. The change is staged in Draft. Active prices and quotes have not changed. Interpolation stays within this exact product, month pair and expiry. Missing strike coverage is reported instead of extrapolated."),
 ("04_preview_quotes.jpg",30,"Preview price and quote changes","The comparison fixes the same market, portfolio and settings.",
  "Preview compares the old and new volatility using exactly the same market and positions. The table shows the premium change and the before and after bid and ask. The explanation connects each quote to inventory, crossing cost, and the marginal change in scenario risk. These are indicative model quotes, with explicit size limits, not exchange orders or executable prices."),
 ("05_preview_hedges.jpg",25,"Inspect integer proxy hedges","Direct CSO offsets are disabled. Review direction, lots, cost and solver status.",
  "The example starts short ten calendar spread option calls. Direct C S O offsets are disabled. The optimizer searches permitted futures and vanilla option contracts, with integer lots and costs. The comparison shows how each suggested position changes. A bounded search result is labeled as limited when optimality has not been proven."),
 ("06_active_stress.jpg",25,"Apply, then inspect residual risk","CSO volatility up, vanilla volatility unchanged: proxy risk remains.",
  "Apply activates one complete result bundle. Pricing, quotes, hedge quantities and risk share the same versions. The stress panel compares no hedge, futures only and the proxy hedge. In the critical basis scenario, C S O volatility rises while vanilla volatility stays unchanged. The remaining loss is visible: the proxy does not eliminate that exposure."),
 ("07_replay_manual.jpg",20,"Refresh prices without overwriting a manual view","Local replay advances stored observations. It is not a live connection.",
  "Now I advance the local replay. Market observations and futures prices update, while manual mode keeps the selected volatility version. A fresh bundle is calculated from those inputs. Missing, stale or invalid data suppress proposals. A failed update cannot present an old hedge recommendation as current."),
 ("08_follow_market.jpg",15,"Return to market and review the evidence","Real snapshot, final-40-session holdout and live soak acceptance remain pending.",
  "Follow market resumes accepted calibrations. The next milestone is an authorized historical snapshot, followed by the final forty session holdout and a sixty minute live acceptance run. No empirical hedge advantage is claimed yet."),
]

def build(reuse_audio=False):
    OUT.mkdir(parents=True,exist_ok=True);WORK.mkdir(parents=True,exist_ok=True)
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    font_path="/System/Library/Fonts/Supplemental/Arial.ttf"
    bold_path="/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    big=ImageFont.truetype(bold_path,40);small=ImageFont.truetype(font_path,26);tiny=ImageFont.truetype(font_path,22)
    manifest=[];elapsed=0
    for index,(filename,seconds,title,subtitle,narration) in enumerate(SEGMENTS,1):
        source=SOURCES/filename
        if not source.exists():raise FileNotFoundError(source)
        canvas=Image.new("RGB",(2560,1440),"#101419");draw=ImageDraw.Draw(canvas)
        draw.text((48,18),f"{index:02d} / 08    {title}",font=big,fill="#E4E8EC")
        draw.text((48,75),subtitle,font=small,fill="#A4C4CC")
        capture=ImageOps.contain(Image.open(source).convert("RGB"),(2490,1220))
        canvas.paste(capture,((2560-capture.width)//2,125+(1220-capture.height)//2))
        draw.rectangle((48,1362,2512,1365),fill="#31434C")
        draw.text((48,1386),"WTI OPTIONS DESK  |  CAPTURED APP WALKTHROUGH  |  ENGINEERING FIXTURE",font=tiny,fill="#D6B885")
        draw.text((2110,1386),f"{elapsed//60}:{elapsed%60:02d} - {(elapsed+seconds)//60}:{(elapsed+seconds)%60:02d}",font=tiny,fill="#AEBAC4")
        frame=WORK/f"frame_{index:02d}.png";canvas.save(frame)
        speech=WORK/f"narration_{index:02d}.txt";speech.write_text(narration)
        audio=WORK/f"narration_{index:02d}.aiff"
        if not reuse_audio:
            subprocess.run(["say","-v","Samantha","-r","165","-f",str(speech),"-o",str(audio)],check=True)
        probe=subprocess.run([ffmpeg,"-i",str(audio)],capture_output=True,text=True)
        duration=re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)",probe.stderr)
        if duration is None or audio.stat().st_size<10000:
            raise RuntimeError("Local speech service produced empty audio; narration not accepted")
        h,m,s=map(float,duration.groups())
        if h*3600+m*60+s>seconds-.01:
            raise RuntimeError(f"Narration {index} exceeds its segment; shorten the text or increase speech rate")
        clip=WORK/f"clip_{index:02d}.mp4"
        subprocess.run([ffmpeg,"-y","-loglevel","error","-loop","1","-framerate","15","-i",str(frame),"-i",str(audio),"-t",str(seconds),"-c:v","libx264","-preset","veryfast","-crf","22","-tune","stillimage","-pix_fmt","yuv420p","-c:a","aac","-ar","48000","-b:a","128k","-af","apad",str(clip)],check=True)
        manifest.append({"screenshot":str(source),"clip":str(clip),"start_seconds":elapsed,"duration_seconds":seconds,"title":title,"narration":narration})
        elapsed+=seconds
    listing=WORK/"concat.txt";listing.write_text("\n".join("file '"+x["clip"].replace("'","'\\''")+"'" for x in manifest))
    output=OUT/"WTI_Options_Desk_3min.mp4"
    subprocess.run([ffmpeg,"-y","-loglevel","error","-f","concat","-safe","0","-i",str(listing),"-c","copy","-movflags","+faststart",str(output)],check=True)
    (OUT/"demo_manifest.json").write_text(json.dumps({"format":"Edited walkthrough using actual application screenshots and local synthetic English narration; not continuous screen recording","duration_seconds":elapsed,"segments":manifest},indent=2))
    (OUT/"demo_transcript.txt").write_text("\n\n".join(f"{s['start_seconds']//60}:{s['start_seconds']%60:02d} {s['title']}\n{s['narration']}" for s in manifest))
    print(output)
    return output

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--reuse-audio",action="store_true")
    build(parser.parse_args().reuse_audio)
