<?php
/**
 * Studio test runner. No PHPUnit: the target host has no composer, and the suite
 * is small enough that an assertion helper is less machinery than a test framework.
 *
 *   php studio/tests/run.php
 *
 * Exits non-zero on failure. Uses a throwaway data directory via QB_DATA, so it
 * never touches a real project.
 */
declare(strict_types=1);

define('QB_BOOT', true);

$tmp = sys_get_temp_dir() . '/qb-test-' . bin2hex(random_bytes(4));
putenv('QB_DATA=' . $tmp);

require __DIR__ . '/../src/core.php';
require __DIR__ . '/../src/prompts.php';
require __DIR__ . '/../src/domain.php';
require __DIR__ . '/../src/images.php';
require __DIR__ . '/../src/export.php';
require __DIR__ . '/../src/review.php';

$failures = 0;
$checks = 0;

function check(string $label, bool $ok, string $detail = ''): void
{
    global $failures, $checks;
    $checks++;
    if ($ok) {
        echo "  ok   $label\n";
        return;
    }
    $failures++;
    echo "  FAIL $label" . ($detail !== '' ? "\n       $detail" : '') . "\n";
}

function is_same(string $label, string $expected, string $actual): void
{
    $detail = '';
    if ($expected !== $actual) {
        $at = 0;
        $len = min(strlen($expected), strlen($actual));
        while ($at < $len && $expected[$at] === $actual[$at]) {
            $at++;
        }
        $detail = "first difference at byte $at\n"
            . 'expected: ' . json_encode(substr($expected, max(0, $at - 30), 90)) . "\n"
            . 'actual  : ' . json_encode(substr($actual, max(0, $at - 30), 90));
    }
    check($label, $expected === $actual, $detail);
}

/** Read a .tar.gz built by qb_tar_gz into name => bytes. Test-side only. */
function tar_entries(string $gz): array
{
    $data = (string) gzdecode($gz);
    $entries = [];
    $off = 0;
    $len = strlen($data);
    while ($off + 512 <= $len) {
        $header = substr($data, $off, 512);
        if (trim($header, "\0") === '') {
            break;
        }
        $name = rtrim(substr($header, 0, 100), "\0");
        $size = (int) octdec(trim(substr($header, 124, 12), " \0"));
        $flag = substr($header, 156, 1);
        $sum = (int) octdec(trim(substr($header, 148, 8), " \0"));
        // Verify the header checksum: a tar writer that gets this wrong produces
        // archives that GNU tar refuses.
        $headerForSum = substr_replace($header, str_repeat(' ', 8), 148, 8);
        $calc = 0;
        for ($i = 0; $i < 512; $i++) {
            $calc += ord($headerForSum[$i]);
        }
        $entries[$name] = [
            'body' => substr($data, $off + 512, $size),
            'checksum_ok' => $calc === $sum,
            'type' => $flag,
        ];
        $off += 512 + (int) (ceil($size / 512) * 512);
    }
    return $entries;
}

echo "Qwen-bookend studio tests\n=========================\n\n";

/* ------------------------------------------------------- schema and settings */
echo "database\n";
db();
check('schema created', is_file(QB_DB));
set_setting('probe', 'value');
check('setting round-trip', setting('probe') === 'value');
set_setting('probe', null);
check('setting delete', setting('probe') === null);
check('password unset at start', !has_password());

/* ------------------------------------------------- prompt conformance (PHP) */
echo "\nprompt conformance (PHP vs Python fixtures)\n";
$fixtures = json_decode((string) file_get_contents(__DIR__ . '/fixtures/prompts.json'), true);
check('fixtures present', is_array($fixtures) && isset($fixtures['cases']));

foreach ($fixtures['cases'] as $case) {
    $r = Prompts::compose($case['body'], $case['labels'], $case['style']);
    is_same('prompt: ' . $case['name'], $case['expected_prompt'], $r['prompt']);
    check(
        'notes: ' . $case['name'],
        $r['notes'] === $case['expected_notes'],
        'expected ' . json_encode($case['expected_notes']) . ' got ' . json_encode($r['notes'])
    );
}

is_same('describe numbering', $fixtures['describe']['expected'], Prompts::describe($fixtures['describe']['labels']));
foreach ($fixtures['styles'] as $key => $text) {
    check("style text: $key", (Prompts::STYLES[$key] ?? null) === $text);
}
check('plate label', Prompts::PLATE_LABEL === $fixtures['plate_label']);
check('start frame label', Prompts::START_FRAME_LABEL === $fixtures['start_frame_label']);

/* ------------------------------------------------------------------ the tar */
echo "\ntar writer\n";
$entries = tar_entries(qb_tar_gz(['a.txt' => "hello\n", 'dir/b.bin' => str_repeat('x', 700)]));
check('two entries', count($entries) === 2, 'got ' . count($entries));
check('small body', ($entries['a.txt']['body'] ?? '') === "hello\n");
check('padded body survives', strlen($entries['dir/b.bin']['body'] ?? '') === 700);
check('header checksums valid', ($entries['a.txt']['checksum_ok'] ?? false) && ($entries['dir/b.bin']['checksum_ok'] ?? false));

/* ------------------------------------------------------------------- export */
echo "\nexport payload\n";
$pid = project_create('Test Project', 'clay', 1280, 768);
$assetId = asset_create($pid, 'character', 'Toby', 'a small brown terrier with a red collar');
$shotId = shot_create($pid, 'shot_001');

// A real PNG, stored the way an upload would be.
$img = imagecreatetruecolor(64, 48);
imagefill($img, 0, 0, imagecolorallocate($img, 120, 160, 90));
$pngPath = $tmp . '/src.png';
imagepng($img, $pngPath);
$stored = store_media($pngPath, 'image/png');
$probe = image_probe(media_path($stored['file']));
image_add($assetId, 'front', $stored, $probe['width'], $probe['height'], $probe['has_alpha']);

shot_update($shotId, [
    'slug' => 'shot_001',
    'start_text' => 'Toby waits by the gate.',
    'stop_text' => 'Toby has turned towards the coop.',
    'motion_prompt' => 'He turns his head.',
    'duration_s' => 5,
]);
shot_set_selection($shotId, [$assetId]);

$result = export_build($pid);
check('export returns an id', $result['id'] !== '');
check('one image copied', $result['images'] === 1);
check('no missing warnings', $result['missing'] === [], json_encode($result['missing']));

$dir = QB_EXPORTS . '/' . $result['id'];
check('project.json written', is_file($dir . '/project.json'));
check('manifest written', is_file($dir . '/reference/manifest.sha256'));
check('bundle written', is_file($dir . '/project.tar.gz'));

$refFiles = array_map('basename', glob($dir . '/reference/*') ?: []);
sort($refFiles);
check('reference files', $refFiles === ['manifest.sha256', 'refs.json', 'toby_front.png'], implode(',', $refFiles));

$refsJson = json_decode((string) file_get_contents($dir . '/reference/refs.json'), true);
check(
    'refs.json label comes from the description',
    ($refsJson['toby_front.png'] ?? '') === 'a small brown terrier with a red collar, front view',
    json_encode($refsJson)
);

$manifest = (string) file_get_contents($dir . '/reference/manifest.sha256');
check('manifest lists the image', str_contains($manifest, 'toby_front.png'));
check('manifest excludes itself', !str_contains($manifest, 'manifest.sha256'));

$shotTar = (string) file_get_contents($dir . '/shots/in/shot_001.tar.gz');
$members = tar_entries($shotTar);
foreach (['start.txt', 'stop.txt', 'prompt.txt', 'use.txt'] as $member) {
    check("shot archive has $member", isset($members[$member]));
}
check('start.txt content', trim($members['start.txt']['body'] ?? '') === 'Toby waits by the gate.');
check('use.txt names the reference', trim($members['use.txt']['body'] ?? '') === 'toby_front.png');
check('no audio written when absent', !isset($members['audio.wav']));

$bundle = tar_entries((string) file_get_contents($dir . '/project.tar.gz'));
check('bundle has reference/', isset($bundle['reference/refs.json']), implode(',', array_keys($bundle)));
check('bundle has shots/in/', isset($bundle['shots/in/shot_001.tar.gz']));
check('bundle has project.json', isset($bundle['project.json']));

/* ------------------------------------------------------- download path safety */
echo "\ndownload safety\n";
check('inside export resolves', export_download_path($result['id'], 'project.tar.gz') !== null);
check('traversal refused', export_download_path($result['id'], '../../app.sqlite') === null);
check('absolute refused', export_download_path($result['id'], '/etc/passwd') === null);
check('missing file refused', export_download_path($result['id'], 'nope.txt') === null);

/* -------------------------------------------------------------- audio parse */
echo "\naudio duration\n";
$wav = $tmp . '/t.wav';
$dataLen = 44100 * 2 * 2 * 2; // 2 s of 44.1 kHz stereo 16-bit = 352,800 bytes
$header = 'RIFF' . pack('V', 36 + $dataLen) . 'WAVE' . 'fmt ' . pack('V', 16)
    . pack('v', 1) . pack('v', 2) . pack('V', 44100) . pack('V', 44100 * 4)
    . pack('v', 4) . pack('v', 16) . 'data' . pack('V', $dataLen);
file_put_contents($wav, $header . str_repeat("\0", $dataLen));
$seconds = audio_duration($wav, 'audio/wav');
check('wav duration is 2 s', $seconds !== null && abs($seconds - 2.0) < 0.01, var_export($seconds, true));
check('non-wav returns null', audio_duration($wav, 'audio/mpeg') === null);

/* ---------------------------------------------------------- image utilities */
echo "\nimage utilities\n";
$big = $tmp . '/big.png';
$src = imagecreatetruecolor(800, 400);
imagefill($src, 0, 0, imagecolorallocate($src, 200, 40, 40));
imagepng($src, $big);
$fitted = image_fit($big, 640, 480, 'cover');
check('cover produces an image', $fitted !== null && is_file($fitted));
if ($fitted !== null) {
    $probe = image_probe($fitted);
    check('cover hits the target size', $probe['width'] === 640 && $probe['height'] === 480,
        $probe['width'] . 'x' . $probe['height']);
}
$padded = image_fit($big, 1280, 768, 'contain');
check('contain produces an image', $padded !== null && is_file($padded));
if ($padded !== null) {
    $probe = image_probe($padded);
    check('contain hits the target size', $probe['width'] === 1280 && $probe['height'] === 768,
        $probe['width'] . 'x' . $probe['height']);
    check('contain leaves transparency', $probe['has_alpha']);
}
$thumb = thumbnail($stored['file'], 32);
check('thumbnail generated', $thumb !== null && is_file($thumb));

/* ------------------------------------------------------------ review metrics */
echo "\nreview metrics\n";
$sameA = $tmp . '/a.png';
$sameB = $tmp . '/b.png';
$im = imagecreatetruecolor(64, 64);
imagefill($im, 0, 0, imagecolorallocate($im, 100, 120, 140));
imagepng($im, $sameA);
imagepng($im, $sameB);
$m = rv_compare($sameA, $sameB);
check('identical pair is stalled', $m['verdict'] === 'stalled', $m['verdict']);
check('identical pair ssim ~1', $m['ssim'] !== null && $m['ssim'] > 0.99, var_export($m['ssim'], true));

$dark = $tmp . '/c.png';
$im = imagecreatetruecolor(64, 64);
imagefill($im, 0, 0, imagecolorallocate($im, 10, 10, 10));
imagepng($im, $dark);
$m2 = rv_compare($sameA, $dark);
check('very different pair is drift', $m2['verdict'] === 'drift', $m2['verdict']);

/* --------------------------------------------------------- erase (testing aid) */
echo "\nerase (testing only)\n";
require __DIR__ . '/../src/erase.php';

set_setting('password_hash', password_hash('erase-test-password', PASSWORD_DEFAULT));
check('password present before the wipe', has_password());

// A directory guard that the wipe must not touch.
file_put_contents(QB_MEDIA . '/.htaccess', "guarded\n");

$stats = erase_stats();
check('stats see the seeded project', (int) $stats['projects'] >= 1, json_encode($stats));
check('stats see media files', (int) $stats['media_files'] >= 1, json_encode($stats));

$result = erase_all(true);
check('projects gone', (int) db()->query('SELECT COUNT(*) FROM projects')->fetchColumn() === 0);
check('assets gone', (int) db()->query('SELECT COUNT(*) FROM assets')->fetchColumn() === 0);
check('images gone', (int) db()->query('SELECT COUNT(*) FROM images')->fetchColumn() === 0);
check('shots gone', (int) db()->query('SELECT COUNT(*) FROM shots')->fetchColumn() === 0);
check('bookends gone', (int) db()->query('SELECT COUNT(*) FROM bookends')->fetchColumn() === 0);
check('media directory emptied', erase_count_files(QB_MEDIA) === 0);
check('thumbs directory emptied', erase_count_files(QB_THUMBS) === 0);
check('exports directory emptied', count(glob(QB_EXPORTS . '/*', GLOB_ONLYDIR) ?: []) === 0);
check('.htaccess guard survives', is_file(QB_MEDIA . '/.htaccess'));
check('password kept by default', has_password());
check('removed files counted', (int) $result['removed_files'] >= 1, json_encode($result['removed_files']));

project_create('Second life');
$reset = erase_all(false);
check('factory reset drops the password', !has_password());
check('factory reset drops the project', (int) db()->query('SELECT COUNT(*) FROM projects')->fetchColumn() === 0);
check('factory reset reports it kept no password', $reset['password_kept'] === false);

/* ----------------------------------------------------------------- teardown */
echo "\n";
echo $failures === 0
    ? "all $checks checks passed\n"
    : "$failures of $checks checks FAILED\n";

exit($failures === 0 ? 0 : 1);
