<?php
/**
 * Image handling with GD only: probing, thumbnails, and the crop/pad utilities
 * that keep every reference on the project's pixel grid before it is ever sent
 * to the model.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

/** @return array{width:int, height:int, mime:string, has_alpha:bool}|null */
function image_probe(string $path): ?array
{
    $info = @getimagesize($path);
    if ($info === false) {
        return null;
    }
    return [
        'width' => (int) $info[0],
        'height' => (int) $info[1],
        'mime' => (string) ($info['mime'] ?? ''),
        'has_alpha' => image_has_alpha($path, (string) ($info['mime'] ?? '')),
    ];
}

/** Sample a grid of pixels rather than every one: enough to spot a cut-out. */
function image_has_alpha(string $path, string $mime): bool
{
    if (!in_array($mime, ['image/png', 'image/webp', 'image/gif'], true)) {
        return false;
    }
    $img = @imagecreatefromstring((string) file_get_contents($path));
    if ($img === false) {
        return false;
    }
    $w = imagesx($img);
    $h = imagesy($img);
    $found = false;
    for ($i = 0; $i < 24 && !$found; $i++) {
        for ($j = 0; $j < 24; $j++) {
            $x = (int) (($i + 0.5) * $w / 24);
            $y = (int) (($j + 0.5) * $h / 24);
            $rgba = imagecolorat($img, $x, $y);
            if ((($rgba >> 24) & 0x7F) > 0) {
                $found = true;
                break;
            }
        }
    }
    return $found;
}

function image_load(string $path): GdImage|false
{
    return @imagecreatefromstring((string) file_get_contents($path));
}

/**
 * Place an image on to a target canvas.
 *
 *   cover    fill the canvas, cropping the overflow (default: keeps subject size)
 *   contain  fit inside the canvas, padding the rest (keeps the whole frame)
 *
 * pad colour is transparent for PNG output so a cut-out stays a cut-out.
 *
 * @return string|null path to a temporary PNG/JPEG, or null on failure
 */
function image_fit(string $path, int $targetW, int $targetH, string $mode = 'cover'): ?string
{
    $src = image_load($path);
    if ($src === false) {
        return null;
    }
    $sw = imagesx($src);
    $sh = imagesy($src);
    if ($sw <= 0 || $sh <= 0) {
        return null;
    }

    $canvas = imagecreatetruecolor($targetW, $targetH);
    imagealphablending($canvas, false);
    imagesavealpha($canvas, true);
    $transparent = imagecolorallocatealpha($canvas, 0, 0, 0, 127);
    imagefilledrectangle($canvas, 0, 0, $targetW, $targetH, $transparent);

    if ($mode === 'contain') {
        $scale = min($targetW / $sw, $targetH / $sh);
        $dw = (int) round($sw * $scale);
        $dh = (int) round($sh * $scale);
        $dx = (int) round(($targetW - $dw) / 2);
        $dy = (int) round(($targetH - $dh) / 2);
        imagecopyresampled($canvas, $src, $dx, $dy, 0, 0, $dw, $dh, $sw, $sh);
    } else {
        $scale = max($targetW / $sw, $targetH / $sh);
        $dw = (int) round($sw * $scale);
        $dh = (int) round($sh * $scale);
        $sx = (int) max(0, round(($dw - $targetW) / 2 / $scale));
        $sy = (int) max(0, round(($dh - $targetH) / 2 / $scale));
        $sw = (int) min($sw, round($targetW / $scale));
        $sh = (int) min($sh, round($targetH / $scale));
        imagecopyresampled($canvas, $src, 0, 0, $sx, $sy, $targetW, $targetH, $sw, $sh);
    }

    $tmp = tempnam(sys_get_temp_dir(), 'qbfit');
    if ($tmp === false) {
        return null;
    }
    $out = $tmp . '.png';
    imagepng($canvas, $out, 6);
    @unlink($tmp);
    return $out;
}

/** Cached thumbnail path for a stored media file. */
function thumbnail(string $rel, int $maxW = 480): ?string
{
    $src = media_path($rel);
    if (!is_file($src)) {
        return null;
    }
    $base = QB_THUMBS . '/' . substr(sha1($rel . ':' . $maxW), 0, 2) . '/';
    if (!is_dir($base)) {
        @mkdir($base, 0775, true);
    }
    $dest = $base . sha1($rel) . '_' . $maxW . '.jpg';
    if (is_file($dest) && filemtime($dest) >= (filemtime($src) ?: 0)) {
        return $dest;
    }
    $img = image_load($src);
    if ($img === false) {
        return null;
    }
    $w = imagesx($img);
    $h = imagesy($img);
    $scale = min(1.0, $maxW / max(1, $w));
    $tw = max(1, (int) round($w * $scale));
    $th = max(1, (int) round($h * $scale));
    $thumb = imagecreatetruecolor($tw, $th);
    $white = imagecolorallocate($thumb, 255, 255, 255);
    imagefilledrectangle($thumb, 0, 0, $tw, $th, $white);
    imagecopyresampled($thumb, $img, 0, 0, 0, 0, $tw, $th, $w, $h);
    imagejpeg($thumb, $dest, 82);
    return $dest;
}

/**
 * WAV duration from the header, without ffmpeg or an audio library.
 * Returns null for formats we do not parse (mp3/ogg) -- the browser player still
 * shows the duration, this is only for the stored frame budget.
 */
function audio_duration(string $path, string $mime): ?float
{
    if (!in_array($mime, ['audio/wav', 'audio/x-wav', 'audio/wave'], true)) {
        return null;
    }
    $fh = @fopen($path, 'rb');
    if ($fh === false) {
        return null;
    }
    $header = fread($fh, 12);
    if ($header === false || substr($header, 0, 4) !== 'RIFF' || substr($header, 8, 4) !== 'WAVE') {
        fclose($fh);
        return null;
    }
    $byteRate = 0;
    $dataSize = 0;
    while (!feof($fh)) {
        $chunk = fread($fh, 8);
        if ($chunk === false || strlen($chunk) < 8) {
            break;
        }
        $id = substr($chunk, 0, 4);
        $size = (int) unpack('V', substr($chunk, 4, 4))[1];
        if ($id === 'fmt ') {
            $fmt = fread($fh, min($size, 40));
            if ($fmt !== false && strlen($fmt) >= 12) {
                $byteRate = (int) unpack('V', substr($fmt, 8, 4))[1];
            }
            if ($size > strlen((string) $fmt)) {
                fseek($fh, $size - strlen((string) $fmt), SEEK_CUR);
            }
        } elseif ($id === 'data') {
            $dataSize = $size;
            break;
        } else {
            fseek($fh, $size + ($size % 2), SEEK_CUR);
        }
    }
    fclose($fh);
    if ($byteRate <= 0) {
        return null;
    }
    return round($dataSize / $byteRate, 3);
}
