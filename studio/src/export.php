<?php
/**
 * Export: write exactly the payload `bookends prep` expects on the rented box.
 *
 *   <export>/reference/            every image used, refs.json, manifest.sha256
 *   <export>/shots/in/<slug>.tar.gz  start.txt, stop.txt, prompt.txt, use.txt, audio.*
 *   <export>/project.json          what this batch is
 *   <export>/project.tar.gz        the whole thing, one download
 *
 * A tar writer exists here because `zip` and `phar` are not guaranteed on shared
 * hosting, while zlib is: tar + gzencode() needs nothing that is not in a stock
 * PHP build. Shot archives are tiny anyway (text + audio); the images travel once,
 * in reference/, shared by every shot through use.txt.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

/** Minimal POSIX ustar writer. @param array<string,string> $entries name => bytes */
function qb_tar(array $entries): string
{
    $out = '';
    foreach ($entries as $name => $data) {
        $name = str_replace('\\', '/', $name);
        if (strlen($name) > 99) {
            $name = substr($name, -99);
        }
        $size = strlen($data);
        $mtime = time();
        $header = str_pad($name, 100, "\0")
            . sprintf('%07o', 0644) . "\0"
            . sprintf('%07o', 0) . "\0"
            . sprintf('%07o', 0) . "\0"
            . sprintf('%011o', $size) . "\0"
            . sprintf('%011o', $mtime) . "\0"
            . str_repeat(' ', 8)
            . '0'
            . str_repeat("\0", 100)
            . "ustar\0" . '00'
            . str_pad('www-data', 32, "\0")
            . str_pad('www-data', 32, "\0")
            . sprintf('%07o', 0) . "\0"
            . sprintf('%07o', 0) . "\0"
            . str_repeat("\0", 155)
            . str_repeat("\0", 12);
        $sum = 0;
        for ($i = 0; $i < 512; $i++) {
            $sum += ord($header[$i]);
        }
        $header = substr_replace($header, sprintf('%06o', $sum) . "\0 ", 148, 8);
        $out .= $header . $data;
        $pad = (512 - ($size % 512)) % 512;
        if ($pad > 0) {
            $out .= str_repeat("\0", $pad);
        }
    }
    return $out . str_repeat("\0", 1024);
}

function qb_tar_gz(array $entries): string
{
    return gzencode(qb_tar($entries), 6);
}

function qb_ext_for_mime(string $mime): string
{
    return match ($mime) {
        'image/png' => 'png',
        'image/jpeg' => 'jpg',
        'image/webp' => 'webp',
        'image/gif' => 'gif',
        default => 'bin',
    };
}

/**
 * Build an export for the given project.
 *
 * @param list<int>|null $shotIds null = every shot
 * @return array{id:string, dir:string, images:int, shots:int, bytes:int, missing:list<string>}
 */
function export_build(int $projectId, ?array $shotIds = null, ?string $style = null): array
{
    $project = project_get($projectId);
    if ($project === null) {
        throw new RuntimeException('unknown project');
    }
    $allShots = shots_for($projectId);
    if ($shotIds !== null) {
        $allShots = array_values(array_filter($allShots, static fn (array $s): bool => in_array((int) $s['id'], $shotIds, true)));
    }
    if ($allShots === []) {
        throw new RuntimeException('no shots selected');
    }

    $exportId = gmdate('Ymd-His') . '-' . substr(bin2hex(random_bytes(3)), 0, 6);
    $dir = QB_EXPORTS . '/' . $exportId;
    $refDir = $dir . '/reference';
    $shotDir = $dir . '/shots/in';
    foreach ([$dir, $refDir, $shotDir] as $d) {
        if (!is_dir($d) && !@mkdir($d, 0775, true) && !is_dir($d)) {
            throw new RuntimeException("cannot create $d");
        }
    }

    // 1. Which images does the whole batch need, and what is each one called?
    $wanted = [];          // asset_id => true
    foreach ($allShots as $shot) {
        foreach (shot_selection((int) $shot['id']) as $assetId) {
            $wanted[$assetId] = true;
        }
    }

    $exportName = [];      // image_id => filename
    $labels = [];          // filename  => label
    $taken = [];
    $imageCount = 0;

    $assets = assets_for($projectId);
    foreach ($assets as $asset) {
        $aid = (int) $asset['id'];
        if (!isset($wanted[$aid])) {
            continue;
        }
        $base = slugify((string) $asset['name']);
        foreach (images_for($aid) as $img) {
            $ext = qb_ext_for_mime((string) $img['mime']);
            $name = $base . '_' . $img['role'] . '.' . $ext;
            $n = 2;
            while (isset($taken[$name])) {
                $name = $base . '_' . $img['role'] . '_' . $n . '.' . $ext;
                $n++;
            }
            $taken[$name] = true;
            $src = media_path((string) $img['file']);
            if (!is_file($src)) {
                continue;
            }
            if (!@copy($src, $refDir . '/' . $name)) {
                throw new RuntimeException("could not copy $name into the export");
            }
            $exportName[(int) $img['id']] = $name;
            $labels[$name] = Prompts::labelFor(
                (string) $asset['kind'],
                (string) $asset['name'],
                $asset['description'] === null ? null : (string) $asset['description'],
                (string) $img['role']
            );
            $imageCount++;
        }
    }

    ksort($labels);
    file_put_contents($refDir . '/refs.json', json_encode($labels, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n");

    // 2. One tiny archive per shot: text, audio, and the list of references it uses.
    $missing = [];
    $shotSummaries = [];
    $audioEntries = [];
    foreach ($allShots as $shot) {
        $slug = (string) $shot['slug'];
        $useNames = [];
        foreach (shot_selection((int) $shot['id']) as $assetId) {
            foreach (images_for($assetId) as $img) {
                if (isset($exportName[(int) $img['id']])) {
                    $useNames[] = $exportName[(int) $img['id']];
                }
            }
        }
        $useNames = array_values(array_unique($useNames));

        $entries = [
            'start.txt' => trim((string) $shot['start_text']) . "\n",
            'stop.txt' => trim((string) $shot['stop_text']) . "\n",
            'prompt.txt' => trim((string) $shot['motion_prompt']) . "\n",
        ];
        if ($useNames !== []) {
            $entries['use.txt'] = implode("\n", $useNames) . "\n";
        }
        if (!empty($shot['audio_file']) && is_file(media_path((string) $shot['audio_file']))) {
            $ext = pathinfo((string) $shot['audio_file'], PATHINFO_EXTENSION) ?: 'wav';
            $entries['audio.' . $ext] = (string) file_get_contents(media_path((string) $shot['audio_file']));
            $audioEntries[$slug] = true;
        }

        file_put_contents($shotDir . '/' . $slug . '.tar.gz', qb_tar_gz($entries));

        if (trim((string) $shot['start_text']) === '' || trim((string) $shot['stop_text']) === '') {
            $missing[] = $slug . ' (empty start or stop text)';
        }
        if ($useNames === []) {
            $missing[] = $slug . ' (no references selected)';
        }
        $shotSummaries[] = [
            'slug' => $slug,
            'start_words' => Prompts::wordCount((string) $shot['start_text']),
            'stop_words' => Prompts::wordCount((string) $shot['stop_text']),
            'references' => $useNames,
            'has_audio' => isset($audioEntries[$slug]),
        ];
    }

    // 3. reference/manifest.sha256 over everything that shipped.
    $lines = [];
    foreach (glob($refDir . '/*') ?: [] as $file) {
        if (basename($file) === 'manifest.sha256') {
            continue;
        }
        $lines[] = hash_file('sha256', $file) . '  ' . basename($file);
    }
    sort($lines);
    file_put_contents($refDir . '/manifest.sha256', implode("\n", $lines) . "\n");

    // 4. project.json and the combined bundle.
    $projectJson = [
        'project' => (string) $project['name'],
        'schema' => 1,
        'size' => [(int) $project['width'], (int) $project['height']],
        'fps' => (int) $project['fps'],
        'style' => $style !== null && $style !== '' ? $style : ($project['style'] ?? null),
        'shot_count' => count($allShots),
        'image_count' => $imageCount,
        'shots' => $shotSummaries,
        'exported_at' => now(),
    ];
    file_put_contents($dir . '/project.json', json_encode($projectJson, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n");

    $bundle = [];
    foreach (array_merge(glob($refDir . '/*') ?: [], glob($dir . '/project.json') ?: []) as $file) {
        $rel = 'reference/' . basename($file);
        if (basename($file) === 'project.json') {
            $rel = 'project.json';
        }
        $bundle[$rel] = (string) file_get_contents($file);
    }
    foreach (glob($shotDir . '/*') ?: [] as $file) {
        $bundle['shots/in/' . basename($file)] = (string) file_get_contents($file);
    }
    file_put_contents($dir . '/project.tar.gz', qb_tar_gz($bundle));

    $bytes = 0;
    foreach (glob($dir . '/*') ?: [] as $f) {
        $bytes += is_file($f) ? (int) filesize($f) : 0;
    }

    return [
        'id' => $exportId,
        'dir' => $dir,
        'images' => $imageCount,
        'shots' => count($allShots),
        'bytes' => $bytes,
        'missing' => $missing,
    ];
}

/** Resolve a download request without letting it escape the export directory. */
function export_download_path(string $exportId, string $relative): ?string
{
    $base = realpath(QB_EXPORTS . '/' . basename($exportId));
    if ($base === false) {
        return null;
    }
    $target = realpath($base . '/' . ltrim($relative, '/'));
    if ($target === false || !is_file($target)) {
        return null;
    }
    if (!str_starts_with($target, $base)) {
        return null;
    }
    return $target;
}

/** Exports on disk, newest first. */
function exports_list(): array
{
    $out = [];
    foreach (glob(QB_EXPORTS . '/*', GLOB_ONLYDIR) ?: [] as $dir) {
        $project = $dir . '/project.json';
        $meta = is_file($project) ? json_decode((string) file_get_contents($project), true) : null;
        $out[] = [
            'id' => basename($dir),
            'project' => is_array($meta) ? (string) ($meta['project'] ?? '?') : '?',
            'shots' => is_array($meta) ? (int) ($meta['shot_count'] ?? 0) : 0,
            'images' => is_array($meta) ? (int) ($meta['image_count'] ?? 0) : 0,
            'bundle_bytes' => is_file($dir . '/project.tar.gz') ? (int) filesize($dir . '/project.tar.gz') : 0,
            'mtime' => is_file($dir . '/project.tar.gz') ? (int) filemtime($dir . '/project.tar.gz') : 0,
        ];
    }
    usort($out, static fn (array $a, array $b): int => $b['mtime'] <=> $a['mtime']);
    return $out;
}
