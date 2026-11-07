import urllib.request
import urllib.parse
from pathlib import Path

# Variable font URLs from google/fonts repo
VARIABLE_FONTS = {
    "Inter": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "Inter-Italic": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter-Italic%5Bopsz%2Cwght%5D.ttf",
    "Oswald": "https://raw.githubusercontent.com/google/fonts/main/ofl/oswald/Oswald%5Bwght%5D.ttf",
    "Lora": "https://raw.githubusercontent.com/google/fonts/main/ofl/lora/Lora%5Bwght%5D.ttf",
    "Lora-Italic": "https://raw.githubusercontent.com/google/fonts/main/ofl/lora/Lora-Italic%5Bwght%5D.ttf",
    "JetBrainsMono": "https://raw.githubusercontent.com/google/fonts/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
    "PlusJakartaSans": "https://raw.githubusercontent.com/google/fonts/main/ofl/plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf",
    "DMSans": "https://raw.githubusercontent.com/google/fonts/main/ofl/dmsans/DMSans%5Bopsz%2Cwght%5D.ttf",
}

# Synonyms/aliases to write so both font-face and pillow find them
ALIASES = {
    "Inter": ["Inter-Regular.ttf", "Inter-Bold.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter.ttf"],
    "Inter-Italic": ["Inter-Italic.ttf"],
    "Oswald": ["Oswald-Regular.ttf", "Oswald-Bold.ttf", "Oswald-Medium.ttf", "Oswald-SemiBold.ttf", "Oswald.ttf"],
    "Lora": ["Lora-Regular.ttf", "Lora-Bold.ttf", "Lora-Medium.ttf", "Lora.ttf"],
    "Lora-Italic": ["Lora-Italic.ttf"],
    "JetBrainsMono": ["JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf", "JetBrainsMono-Medium.ttf", "JetBrainsMono.ttf"],
    "PlusJakartaSans": ["PlusJakartaSans-Regular.ttf", "PlusJakartaSans-Bold.ttf", "PlusJakartaSans-Medium.ttf", "PlusJakartaSans.ttf"],
    "DMSans": ["DMSans-Regular.ttf", "DMSans-Bold.ttf", "DMSans-Medium.ttf", "DMSans.ttf"],
}

root = Path(__file__).resolve().parent.parent
targets = [
    root / "public" / "font-library",
    root / "fonts",
]

for t in targets:
    t.mkdir(parents=True, exist_ok=True)

headers = {"User-Agent": "Mozilla/5.0"}

for key, url in VARIABLE_FONTS.items():
    print(f"Downloading {key} from {url}...")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if len(data) < 1000:
                print(f"Warning: {key} data too small ({len(data)} bytes)")
                continue
            
            # Save for all aliases in both directories
            filenames = ALIASES.get(key, [f"{key}.ttf"])
            for fn in filenames:
                for t in targets:
                    dest = t / fn
                    with open(dest, "wb") as f:
                        f.write(data)
            print(f"[SUCCESS] Saved {key} as {len(filenames)} alias files ({len(data)} bytes each)")
    except Exception as e:
        print(f"Error downloading {key}: {e}")

print("Font download process completed.")
