#!/usr/bin/env python3
"""Build a portable, offline gallery from the real round-device renderer."""
import argparse
import html
import json
import re
from pathlib import Path
import statistics
import time
import zlib

from characters import FRAMES, STATES, load_character
from device import rgb565
from device_ui import render


NOTES = {
    "working": "The selected session is running. Steam and a small bob mean activity, with no invented progress percentage.",
    "waiting": "The selected session needs input. A steady attention marker keeps the request readable.",
    "error": "The selected session reports an error. Open T3 to inspect it; a character cannot approve or retry work.",
    "done": "T3 reports a recently completed turn. This does not certify that the whole task succeeded.",
    "idle": "The selected session is at rest. Resting is different from a successful completion.",
    "offline": "T3 is unavailable. The physical controller replaces the character with its reconnect screen.",
    "unknown": "The selected destination has no local state telemetry. Remote voice routing can still be available.",
}


def build_preview(output, names=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    packs = [load_character(name) for name in names or ("reef", "manta", "octo")]
    if len({pack.id for pack in packs}) != len(packs):
        raise ValueError("Preview character ids must be unique.")
    references, timings, payloads = {}, [], []
    for pack in packs:
        references[pack.id] = {}
        for state in STATES:
            references[pack.id][state] = []
            screen = dict(kind="home", character=pack.id, activity=state, project="Shark lab",
                          title="Make something useful", enabled=True, autoSend=True,
                          index=1, total=3, new=False, browser=False)
            for frame in range(FRAMES):
                before = time.perf_counter()
                image = render(screen, pack, frame)
                timings.append((time.perf_counter() - before) * 1000)
                name = f"{pack.id}-{state}-{frame}.png"
                image.save(output / name)
                references[pack.id][state].append(name)
                payloads.append(len(zlib.compress(rgb565(image))))
    metrics = dict(characters=len(packs), states=len(STATES), frames=len(timings),
                   medianRenderMs=round(statistics.median(timings), 2), maxZlibBytes=max(payloads),
                   firmwareZlibLimit=65536, targetFps=2, source="synthetic production renderer; no USB")
    if max(payloads) > metrics["firmwareZlibLimit"]:
        raise ValueError("A preview frame exceeds the firmware's 64 KiB compressed-frame limit.")
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    for absolute, filename in ((False, "index.html"), (True, "inline.html")):
        images = {pack: {state: [str(output / name) if absolute else name for name in frames]
                         for state, frames in states.items()} for pack, states in references.items()}
        (output / filename).write_text(gallery_html(packs, images))
    return metrics


def gallery_html(packs, images):
    first = packs[0].id
    choices = "".join(f'<button type="button" data-character="{p.id}" aria-pressed="{str(i == 0).lower()}">{html.escape(p.name)}</button>'
                      for i, p in enumerate(packs))
    options = "".join(f'<option value="{state}">{label}</option>' for state, (label, _) in STATES.items())
    template = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Shark companion characters</title>
<style>
:root{color-scheme:dark;--foreground:#f1f0eb;--muted-foreground:#a8aba8;--border:#454745;--accent:#ffad72;--radius:18px;--font-sans:system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;color:var(--foreground);font:15px/1.5 var(--font-sans)}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,.85fr);gap:28px;align-items:center}
.glass{margin:0;min-width:0}.glass img{width:100%;height:auto;max-width:466px;display:block;border-radius:50%;aspect-ratio:1}
figcaption{color:var(--muted-foreground);font-size:12px;margin-top:10px;text-align:center}
h2{font-size:25px;line-height:1.15;letter-spacing:-.7px;font-weight:600;margin:0 0 12px}p{margin:10px 0 16px}
.eyebrow{font:11px ui-monospace,monospace;letter-spacing:1.5px;color:var(--accent);margin:0 0 12px}
.choices{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}button,select{font:inherit;color:var(--foreground);background:transparent;border:1px solid var(--border);border-radius:18px;padding:10px 13px;min-height:44px}
button{cursor:pointer}button[aria-pressed=true]{background:#334441;border-color:#88bcb1}button:focus-visible,select:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
select{display:block;width:100%;margin:7px 0 10px;background:#202627}label{font-size:13px;color:var(--muted-foreground)}
#state-note{min-height:92px;font-size:13px;color:var(--muted-foreground)}.small{font-size:12px;color:var(--muted-foreground)}
@media(max-width:560px){main{grid-template-columns:1fr;gap:22px}.glass{max-width:360px;margin:auto}h2{font-size:23px}#state-note{min-height:0}}
</style><main>
<figure class="glass"><img id="device-image" src="__FIRST_IMAGE__" alt="Round companion showing Reef shark cooking for the selected session" width="466" height="466"><figcaption>Actual 466 × 466 renderer · synthetic session</figcaption></figure>
<section aria-label="Character preview controls"><p class="eyebrow">SHARK HARNESS</p><h2>A little company<br>while work cooks.</h2>
<p>Original sea creatures with a useful job: show what your selected session needs.</p><div class="choices" role="group" aria-label="Character">__CHOICES__</div>
<label for="character-state">Selected session state</label><select id="character-state">__OPTIONS__</select><p id="state-note" aria-live="polite"></p>
<button id="motion" type="button">Pause motion</button><p class="small">Two frames per second. Voice controls take priority.<br>After 60 seconds idle, the device rests; the first touch or BOOT wakes it.</p></section></main>
<script>
const frames=__IMAGES__, notes=__NOTES__, names=__NAMES__, labels=__LABELS__;
let character=__FIRST__, state='working', frame=0;
const preference=matchMedia('(prefers-reduced-motion: reduce)');
let playing=!preference.matches;
const image=document.querySelector('#device-image'),motion=document.querySelector('#motion');
function paint(){image.src=frames[character][state][frame];image.alt=`Round companion showing ${names[character]}: ${labels[state]}`;motion.textContent=playing?'Pause motion':'Play motion';motion.setAttribute('aria-pressed',String(playing));}
function change(){frame=0;document.querySelector('#state-note').textContent=notes[state];paint();}
document.querySelectorAll('[data-character]').forEach(button=>button.addEventListener('click',()=>{character=button.dataset.character;document.querySelectorAll('[data-character]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));change();}));
document.querySelector('#character-state').addEventListener('change',event=>{state=event.target.value;change();});
motion.addEventListener('click',()=>{playing=!playing;frame=0;paint();});
preference.addEventListener('change',()=>{if(preference.matches){playing=false;frame=0;paint();}});
setInterval(()=>{if(playing&&!document.hidden){frame=(frame+1)%8;paint();}},500);
change();
</script></html>"""
    def script_json(value):
        return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    replacements = {"__FIRST_IMAGE__": html.escape(images[first]["working"][0], quote=True),
                    "__CHOICES__": choices, "__OPTIONS__": options,
                    "__IMAGES__": script_json(images), "__NOTES__": script_json(NOTES),
                    "__NAMES__": script_json({p.id: p.name for p in packs}),
                    "__LABELS__": script_json({state: label for state, (label, _) in STATES.items()}),
                    "__FIRST__": script_json(first)}
    # Substitute only the original template. Contributor text may itself look
    # like a placeholder and must never be interpreted a second time.
    return re.sub(r"__[A-Z_]+__", lambda match: replacements[match.group()], template)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/characters"))
    parser.add_argument("--character", action="append", help="Bundled id or local JSON path; repeat to compare.")
    args = parser.parse_args()
    try:
        metrics = build_preview(args.output, args.character)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"Character preview: {error}\n")
    print(json.dumps(metrics, indent=2))
    print(f"Open {args.output / 'index.html'} in a browser. No server or device is needed.")


if __name__ == "__main__":
    main()
