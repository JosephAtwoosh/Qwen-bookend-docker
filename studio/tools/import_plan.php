<?php

declare(strict_types=1);

/**
 * Import the current film project into the studio database.
 *
 * Uses the studio's own domain functions (project_create, asset_create, image_add,
 * shot_create, shot_update, shot_set_selection, bookend_set) rather than raw SQL, so
 * the data satisfies the same invariants the UI enforces and the export depends on.
 *
 * Sources, all of which already exist in the workspace:
 *   plan/beats-scene-01.md            start/stop text and shot durations
 *   plan/refs-farm/*.png              character references, composited into the yard
 *   plan/refs/sets/*.png              the set plates
 *   .test-tmp/scene01/shots/work/<slug>/   motion prompt, selection, rendered bookends
 *
 *   php studio/tools/import_plan.php [--replace]
 *
 * Without --replace it refuses to run if the project already exists, so a second run
 * cannot silently duplicate sixteen shots.
 */

$studio = dirname(__DIR__);
$repo = dirname($studio);
$ws = dirname($repo);

// core.php guards itself: a direct request to src/ must 404, so it exits unless the
// front controller set QB_BOOT. A CLI importer is a legitimate front controller.
define('QB_BOOT', true);

require $studio . '/src/core.php';
require $studio . '/src/images.php';
require $studio . '/src/domain.php';

const PROJECT_NAME = 'Cherry Tree Farm';

$beatsPath = $ws . '/plan/beats-scene-01.md';
$refsFarm = $ws . '/plan/refs-farm';
$plateDir = $ws . '/plan/refs/sets';
$workDir = $ws . '/.test-tmp/scene01/shots/work';

$replace = in_array('--replace', $argv, true);

foreach ([$beatsPath, $refsFarm, $workDir] as $needed) {
    if (!file_exists($needed)) {
        fwrite(STDERR, "missing input: {$needed}\n");
        exit(2);
    }
}

/** Character slug -> display name. Slugs come from the reference filenames. */
const CHARACTERS = [
    'frederick' => 'Frederick',
    'mildred' => 'Mildred',
    'hilda' => 'Hilda',
    'gerry' => 'Gerry',
    'henry' => 'Henry',
    'hubbard' => 'Mrs Hubbard',
    'petra' => 'Petra',
    'oliver' => 'Oliver',
];

/** Read the beats table: number -> duration, start text, stop text. */
function read_beats(string $path): array
{
    $rows = [];
    foreach (file($path, FILE_IGNORE_NEW_LINES) ?: [] as $line) {
        if (!str_starts_with($line, '|')) {
            continue;
        }
        $cells = array_map('trim', explode('|', trim($line, '| ')));
        if (count($cells) < 5 || !ctype_digit($cells[0])) {
            continue;
        }
        $rows[(int) $cells[0]] = [
            'duration' => (float) rtrim($cells[1], 's'),
            'subject' => $cells[2],
            'start' => $cells[3],
            'stop' => $cells[4],
        ];
    }
    return $rows;
}

/** Store a file and return [stored, width, height]. */
function import_file(string $path, string $mime): array
{
    $size = @getimagesize($path);
    $stored = store_media($path, $mime);
    return [$stored, (int) ($size[0] ?? 0), (int) ($size[1] ?? 0)];
}

$beats = read_beats($beatsPath);
printf("beats: %d shots\n", count($beats));

// ------------------------------------------------------------------ project
$existing = null;
foreach (projects_all() as $project) {
    if ($project['name'] === PROJECT_NAME) {
        $existing = $project;
    }
}
if ($existing !== null) {
    if (!$replace) {
        fwrite(STDERR, "project '" . PROJECT_NAME . "' already exists; pass --replace to rebuild it\n");
        exit(3);
    }
    project_delete((int) $existing['id']);
    echo "deleted the existing project\n";
}
$projectId = project_create(PROJECT_NAME, 'pixar', 1280, 768, 24);
printf("project #%d created\n", $projectId);

// -------------------------------------------------------------------- cast
$assetBySlug = [];
$imageCount = 0;

foreach (CHARACTERS as $slug => $name) {
    $files = glob($refsFarm . '/' . $slug . '_*.png') ?: [];
    if ($files === []) {
        printf("  no references for %s\n", $name);
        continue;
    }
    $assetId = asset_create($projectId, 'character', $name);
    $assetBySlug[$slug] = $assetId;
    $position = 0;
    foreach ($files as $file) {
        $role = substr(basename($file, '.png'), strlen($slug) + 1);
        [$stored, $w, $h] = import_file($file, 'image/png');
        image_add($assetId, $role, $stored, $w, $h, false);
        $position++;
        $imageCount++;
    }
    printf("  %-12s %d view(s)\n", $name, $position);
}

// The set is an asset too, so shots can select it like a character.
$setFiles = glob($plateDir . '/*.png') ?: [];
if ($setFiles !== []) {
    $setId = asset_create($projectId, 'set', 'Cherry Tree Farm', 'The yard, from the winter and spring plates.');
    $assetBySlug['cherry_tree_farm'] = $setId;
    foreach ($setFiles as $file) {
        [$stored, $w, $h] = import_file($file, 'image/png');
        image_add($setId, 'plate', $stored, $w, $h, false);
        $imageCount++;
    }
    printf("  %-12s %d plate(s)\n", 'Cherry Tree Farm', count($setFiles));
}

// ------------------------------------------------------------------- shots
$shotCount = 0;
$bookendCount = 0;
$dirs = glob($workDir . '/sc01_*', GLOB_ONLYDIR) ?: [];
sort($dirs);

foreach ($dirs as $dir) {
    $slug = basename($dir);
    $num = (int) substr($slug, -3);
    $start = @file_get_contents($dir . '/start.txt') ?: '';
    $stop = @file_get_contents($dir . '/stop.txt') ?: '';
    $motion = @file_get_contents($dir . '/prompt.txt') ?: '';
    if (trim($start) === '' || trim($stop) === '') {
        printf("  skip %s: missing start or stop text\n", $slug);
        continue;
    }

    $shotId = shot_create($projectId, $slug);
    shot_update($shotId, [
        'start_text' => trim($start),
        'stop_text' => trim($stop),
        'motion_prompt' => trim($motion),
        'duration_s' => $beats[$num]['duration'] ?? 0.0,
        'position' => $num,
    ]);

    $selection = [];
    foreach (file($dir . '/use.txt') ?: [] as $line) {
        $stem = trim($line);
        if ($stem === '') {
            continue;
        }
        $stem = preg_replace('/\.(png|jpe?g)$/i', '', $stem);
        $key = str_starts_with($stem, 'cherry_tree_farm') ? 'cherry_tree_farm' : explode('_', $stem)[0];
        if (isset($assetBySlug[$key])) {
            $selection[] = $assetBySlug[$key];
        }
    }
    if ($selection !== []) {
        shot_set_selection($shotId, $selection);
    }

    foreach (['start', 'stop'] as $tag) {
        $file = $dir . '/' . $tag . '.jpg';
        if (!is_file($file)) {
            continue;
        }
        [$stored, $w, $h] = import_file($file, 'image/jpeg');
        bookend_set($shotId, $tag, $stored, $w, $h);
        $bookendCount++;
    }

    $shotCount++;
    printf("  %s  %d ref(s)  %s\n", $slug, count($selection), $beats[$num]['subject'] ?? '');
}

printf(
    "\nimported: %d characters + 1 set, %d reference images, %d shots, %d bookends\n",
    count($assetBySlug) - 1,
    $imageCount,
    $shotCount,
    $bookendCount,
);
printf("database: %s\n", QB_DB);
