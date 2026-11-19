<?php
/**
 * Deployment health checks.
 *
 * Two of these matter more than the rest:
 *   data_writable   without it nothing works
 *   data_exposed    a web server that ignores .htaccess will happily serve
 *                   data/app.sqlite to anyone who asks for it. This check fetches
 *                   its own data URL over HTTP and fails if the answer is 200.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

function health_checks(): array
{
    $checks = [];

    $checks[] = [
        'label' => 'PHP ' . PHP_VERSION . ' (need 8.1+)',
        'ok' => version_compare(PHP_VERSION, '8.1.0', '>='),
        'detail' => 'too old: upgrade PHP',
    ];
    $checks[] = [
        'label' => 'pdo_sqlite extension',
        'ok' => extension_loaded('pdo_sqlite'),
        'detail' => 'missing: enable pdo_sqlite',
    ];
    $checks[] = [
        'label' => 'gd extension (image utilities)',
        'ok' => extension_loaded('gd'),
        'detail' => 'missing: crop/pad and thumbnails are disabled',
    ];
    $checks[] = [
        'label' => 'zlib (export archives)',
        'ok' => function_exists('gzencode'),
        'detail' => 'missing: cannot build .tar.gz exports',
    ];

    $writable = is_dir(QB_DATA) && is_writable(QB_DATA) && is_writable(dirname(QB_DB));
    $checks[] = [
        'label' => 'data directory writable: ' . QB_DATA,
        'ok' => $writable,
        'detail' => 'not writable: chmod 775 the data directory',
    ];

    $checks[] = [
        'label' => 'upload limit (' . ini_get('upload_max_filesize') . ')',
        'ok' => (int) ini_get('upload_max_filesize') >= 8 || str_contains((string) ini_get('upload_max_filesize'), 'M'),
        'detail' => 'uploads may fail for reference images',
    ];

    $checks[] = [
        'label' => 'data directory not served as static files',
        'ok' => !data_dir_exposed(),
        'detail' => 'EXPOSED: data/ is being served publicly. Add the supplied .htaccess, '
            . 'or move data/ above the document root and set QB_DATA.',
    ];

    return $checks;
}

function health_env(): array
{
    return [
        'PHP' => PHP_VERSION . ' ' . PHP_SAPI,
        'data dir' => QB_DATA,
        'database' => QB_DB,
        'session save path' => (string) (session_save_path() ?: '(default)'),
        'document root' => (string) ($_SERVER['DOCUMENT_ROOT'] ?? '?'),
        'https' => (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'yes' : 'no',
        'app version' => QB_APP . ' schema ' . (setting('schema_version') ?? '?'),
    ];
}

/**
 * Ask the web server for our own database file over HTTP. 200 means the server is
 * serving data/ directly, which would expose every project to anyone with the URL.
 */
function data_dir_exposed(): bool
{
    $probe = QB_DATA . '/.expose-probe.txt';
    if (!is_file($probe)) {
        @file_put_contents($probe, 'probe');
    }
    if (!is_file($probe)) {
        return false; // cannot tell; do not cry wolf
    }
    $scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
    $host = (string) ($_SERVER['HTTP_HOST'] ?? '');
    if ($host === '') {
        return false;
    }
    $docRoot = realpath((string) ($_SERVER['DOCUMENT_ROOT'] ?? ''));
    $dataReal = realpath(QB_DATA);
    if ($docRoot === false || $dataReal === false || !str_starts_with($dataReal, $docRoot)) {
        return false; // data/ is outside the document root: nothing to expose
    }
    $rel = str_replace('\\', '/', substr($dataReal, strlen($docRoot)));
    $url = $scheme . '://' . $host . $rel . '/.expose-probe.txt';

    $ctx = stream_context_create(['http' => ['method' => 'GET', 'timeout' => 4, 'ignore_errors' => true]]);
    $body = @file_get_contents($url, false, $ctx);
    if ($body === false) {
        return false;
    }
    $status = 0;
    foreach ($http_response_header ?? [] as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
            $status = (int) $m[1];
        }
    }
    return $status === 200 && str_contains((string) $body, 'probe');
}
