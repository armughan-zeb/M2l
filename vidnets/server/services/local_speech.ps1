param([Parameter(Mandatory=$true)][string]$InputPath,
      [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath $InputPath -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Speech.AudioFormat;
using System.Speech.Synthesis;
public sealed class VidnetsSpeechWord {
    public string word; public double start; public int position; public int count;
}
public static class VidnetsOfflineSpeech {
    public static List<VidnetsSpeechWord> Render(string text, string output) {
        var words = new List<VidnetsSpeechWord>();
        using (var speech = new SpeechSynthesizer()) {
            speech.Volume = 100;
            foreach (var installed in speech.GetInstalledVoices()) {
                if (installed.Enabled && installed.VoiceInfo.Culture.Name.StartsWith("en")) {
                    speech.SelectVoice(installed.VoiceInfo.Name); break;
                }
            }
            speech.SpeakProgress += (sender, args) => {
                var count = Math.Min(args.CharacterCount, text.Length - args.CharacterPosition);
                if (count > 0) words.Add(new VidnetsSpeechWord {
                    word = text.Substring(args.CharacterPosition, count),
                    start = args.AudioPosition.TotalSeconds,
                    position = args.CharacterPosition, count = count
                });
            };
            speech.SetOutputToWaveFile(output, new SpeechAudioFormatInfo(16000, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
            speech.Speak(text);
            speech.SetOutputToNull();
        }
        return words;
    }
}
'@
$words = [VidnetsOfflineSpeech]::Render([string]$request.text, $OutputPath)
ConvertTo-Json -InputObject @($words) -Depth 4 -Compress | Set-Content -LiteralPath ($OutputPath + '.words.json') -Encoding UTF8
