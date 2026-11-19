<?php
/**
 * Review metrics for a start/stop pair -- a PHP port of bookends/review.py.
 *
 * Same scale (256 px wide grayscale), same box-window SSIM, same thresholds, so
 * the studio's red/amber flags agree in practice with `bookends check`. The CLI
 * remains authoritative: it runs on the rendered originals at full precision,
 * this runs on JPEGs pulled back and is a pre-screen.
 *
 * Results are cached by content hash, because the review page shows many pairs.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

const RV_SSIM_HIGH = 0.97;
const RV_SSIM_LOW = 0.55;
const RV_HUE_HIGH = 0.35;
const RV_WORK_WIDTH = 256;

/** @return array{data:list<float>, w:int, h:int}|null */
function rv_gray(string $path, int $width = RV_WORK_WIDTH): ?array
{
    $img = image_load($path);
    if ($img === false) {
        return null;
    }
    $sw = imagesx($img);
    $sh = imagesy($img);
    if ($sw < 1 || $sh < 1) {
        return null;
    }
    $th = max(1, (int) round($sh * ($width / $sw)));
    $dst = imagecreatetruecolor($width, $th);
    imagecopyresampled($dst, $img, 0, 0, 0, 0, $width, $th, $sw, $sh);

    $data = [];
    for ($y = 0; $y < $th; $y++) {
        for ($x = 0; $x < $width; $x++) {
            $rgb = imagecolorat($dst, $x, $y);
            $r = ($rgb >> 16) & 0xFF;
            $g = ($rgb >> 8) & 0xFF;
            $b = $rgb & 0xFF;
            $data[] = 0.299 * $r + 0.587 * $g + 0.114 * $b;
        }
    }
    return ['data' => $data, 'w' => $width, 'h' => $th];
}

/**
 * Mean over a (2r+1)^2 window with edge padding, via an integral image.
 * @param list<float> $a
 * @return list<float>
 */
function rv_box_mean(array $a, int $w, int $h, int $r): array
{
    $k = 2 * $r + 1;
    $pw = $w + 2 * $r;
    $ph = $h + 2 * $r;

    $pad = [];
    for ($y = 0; $y < $ph; $y++) {
        $sy = min($h - 1, max(0, $y - $r));
        for ($x = 0; $x < $pw; $x++) {
            $sx = min($w - 1, max(0, $x - $r));
            $pad[$y * $pw + $x] = $a[$sy * $w + $sx];
        }
    }

    $iw = $pw + 1;
    $ii = array_fill(0, ($ph + 1) * $iw, 0.0);
    for ($y = 0; $y < $ph; $y++) {
        $rowSum = 0.0;
        for ($x = 0; $x < $pw; $x++) {
            $rowSum += $pad[$y * $pw + $x];
            $ii[($y + 1) * $iw + ($x + 1)] = $ii[$y * $iw + ($x + 1)] + $rowSum;
        }
    }

    $out = [];
    $den = (float) ($k * $k);
    for ($y = 0; $y < $h; $y++) {
        for ($x = 0; $x < $w; $x++) {
            $sum = $ii[($y + $k) * $iw + ($x + $k)]
                 - $ii[$y * $iw + ($x + $k)]
                 - $ii[($y + $k) * $iw + $x]
                 + $ii[$y * $iw + $x];
            $out[] = $sum / $den;
        }
    }
    return $out;
}

/** @param array{data:list<float>, w:int, h:int} $a */
function rv_ssim(array $a, array $b, int $r = 5): ?float
{
    $w = min($a['w'], $b['w']);
    $h = min($a['h'], $b['h']);
    if ($w < 3 || $h < 3) {
        return null;
    }
    $ga = [];
    $gb = [];
    for ($y = 0; $y < $h; $y++) {
        for ($x = 0; $x < $w; $x++) {
            $ga[] = $a['data'][$y * $a['w'] + $x];
            $gb[] = $b['data'][$y * $b['w'] + $x];
        }
    }
    $aa = array_map(static fn (float $v): float => $v * $v, $ga);
    $bb = array_map(static fn (float $v): float => $v * $v, $gb);
    $ab = [];
    $n = count($ga);
    for ($i = 0; $i < $n; $i++) {
        $ab[] = $ga[$i] * $gb[$i];
    }

    $muA = rv_box_mean($ga, $w, $h, $r);
    $muB = rv_box_mean($gb, $w, $h, $r);
    $eA2 = rv_box_mean($aa, $w, $h, $r);
    $eB2 = rv_box_mean($bb, $w, $h, $r);
    $eAB = rv_box_mean($ab, $w, $h, $r);

    $c1 = (0.01 * 255) ** 2;
    $c2 = (0.03 * 255) ** 2;
    $sum = 0.0;
    for ($i = 0; $i < $n; $i++) {
        $va = $eA2[$i] - $muA[$i] * $muA[$i];
        $vb = $eB2[$i] - $muB[$i] * $muB[$i];
        $cab = $eAB[$i] - $muA[$i] * $muB[$i];
        $num = (2 * $muA[$i] * $muB[$i] + $c1) * (2 * $cab + $c2);
        $den = ($muA[$i] ** 2 + $muB[$i] ** 2 + $c1) * ($va + $vb + $c2);
        $sum += $den == 0.0 ? 1.0 : $num / $den;
    }
    return $sum / $n;
}

/** @return list<float> 256-bin normalised hue histogram */
function rv_hue_hist(string $path, int $width = 128): array
{
    $img = image_load($path);
    $hist = array_fill(0, 256, 0.0);
    if ($img === false) {
        return $hist;
    }
    $sw = imagesx($img);
    $sh = imagesy($img);
    $th = max(1, (int) round($sh * ($width / $sw)));
    $dst = imagecreatetruecolor($width, $th);
    imagecopyresampled($dst, $img, 0, 0, 0, 0, $width, $th, $sw, $sh);

    for ($y = 0; $y < $th; $y++) {
        for ($x = 0; $x < $width; $x++) {
            $rgb = imagecolorat($dst, $x, $y);
            $r = (($rgb >> 16) & 0xFF) / 255.0;
            $g = (($rgb >> 8) & 0xFF) / 255.0;
            $b = ($rgb & 0xFF) / 255.0;
            $max = max($r, $g, $b);
            $min = min($r, $g, $b);
            $delta = $max - $min;
            if ($delta == 0.0) {
                continue; // achromatic: no hue to compare
            }
            if ($max === $r) {
                $hue = fmod(($g - $b) / $delta, 6.0);
            } elseif ($max === $g) {
                $hue = ($b - $r) / $delta + 2.0;
            } else {
                $hue = ($r - $g) / $delta + 4.0;
            }
            $hue = $hue / 6.0;
            if ($hue < 0) {
                $hue += 1.0;
            }
            $bin = min(255, max(0, (int) floor($hue * 256)));
            $hist[$bin] += 1.0;
        }
    }
    $total = array_sum($hist);
    if ($total > 0) {
        foreach ($hist as $i => $v) {
            $hist[$i] = $v / $total;
        }
    }
    return $hist;
}

/**
 * @return array{ssim:?float, mad:?float, hue:?float, verdict:string, reasons:list<string>}
 */
function rv_compare(string $startPath, string $stopPath): array
{
    $cacheDir = QB_THUMBS . '/metrics';
    if (!is_dir($cacheDir)) {
        @mkdir($cacheDir, 0775, true);
    }
    $key = sha1($startPath . '|' . $stopPath . '|' . (filemtime($startPath) ?: 0) . '|' . (filemtime($stopPath) ?: 0));
    $cacheFile = $cacheDir . '/' . $key . '.json';
    if (is_file($cacheFile)) {
        $cached = json_decode((string) file_get_contents($cacheFile), true);
        if (is_array($cached)) {
            return $cached;
        }
    }

    $ga = rv_gray($startPath);
    $gb = rv_gray($stopPath);
    if ($ga === null || $gb === null) {
        return ['ssim' => null, 'mad' => null, 'hue' => null, 'verdict' => 'unreadable', 'reasons' => ['could not read one of the frames']];
    }

    $ssim = rv_ssim($ga, $gb);
    $mad = null;
    $n = min(count($ga['data']), count($gb['data']));
    if ($n > 0) {
        $sum = 0.0;
        for ($i = 0; $i < $n; $i++) {
            $sum += abs($ga['data'][$i] - $gb['data'][$i]);
        }
        $mad = $sum / $n;
    }
    $ha = rv_hue_hist($startPath);
    $hb = rv_hue_hist($stopPath);
    $hue = 0.0;
    for ($i = 0; $i < 256; $i++) {
        $hue += abs($ha[$i] - $hb[$i]);
    }
    $hue *= 0.5;

    $reasons = [];
    if ($ssim === null) {
        $verdict = 'unreadable';
    } elseif ($ssim >= RV_SSIM_HIGH) {
        $verdict = 'stalled';
        $reasons[] = sprintf('SSIM %.3f >= %.2f: start and stop are nearly the same frame, so there is almost no motion to interpolate', $ssim, RV_SSIM_HIGH);
    } elseif ($ssim <= RV_SSIM_LOW) {
        $verdict = 'drift';
        $reasons[] = sprintf('SSIM %.3f <= %.2f: the pair has little in common; check lighting, background and framing', $ssim, RV_SSIM_LOW);
    } elseif ($hue > RV_HUE_HIGH) {
        $verdict = 'drift';
        $reasons[] = sprintf('hue distance %.3f > %.2f: the colour balance moved between frames', $hue, RV_HUE_HIGH);
    } else {
        $verdict = 'pass';
    }

    $result = [
        'ssim' => $ssim === null ? null : round($ssim, 4),
        'mad' => $mad === null ? null : round($mad, 2),
        'hue' => round($hue, 4),
        'verdict' => $verdict,
        'reasons' => $reasons,
    ];
    @file_put_contents($cacheFile, json_encode($result));
    return $result;
}
