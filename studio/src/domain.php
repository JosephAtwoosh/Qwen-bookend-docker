<?php
/**
 * Domain queries. Plain functions over PDO: the app is small enough that a
 * repository class per entity would be more ceremony than structure.
 */
declare(strict_types=1);

if (!defined('QB_BOOT')) {
    http_response_code(404);
    exit;
}

const ASSET_KINDS = ['character' => 'Character', 'prop' => 'Prop', 'set' => 'Set'];
const IMAGE_ROLES = [
    'front' => 'Front',
    'three_quarter' => 'Three-quarter',
    'side' => 'Side',
    'back' => 'Back',
    'detail' => 'Detail',
    'plate' => 'Empty plate',
    'other' => 'Other',
];

/* ------------------------------------------------------------------ projects */

function projects_all(): array
{
    return db()->query('SELECT p.*,
        (SELECT COUNT(*) FROM assets a WHERE a.project_id = p.id) AS asset_count,
        (SELECT COUNT(*) FROM shots s WHERE s.project_id = p.id) AS shot_count
        FROM projects p ORDER BY p.updated_at DESC')->fetchAll();
}

function project_get(int $id): ?array
{
    $st = db()->prepare('SELECT * FROM projects WHERE id = ?');
    $st->execute([$id]);
    return $st->fetch() ?: null;
}

function project_create(string $name, string $style = '', int $width = 1280, int $height = 768, int $fps = 24): int
{
    $name = trim($name) !== '' ? trim($name) : 'Untitled project';
    $st = db()->prepare('INSERT INTO projects(name, slug, width, height, style, fps, notes, created_at, updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?)');
    $st->execute([$name, slugify($name), $width, $height, $style === '' ? null : $style, $fps, null, now(), now()]);
    return (int) db()->lastInsertId();
}

function project_update(int $id, array $data): void
{
    $st = db()->prepare('UPDATE projects SET name = ?, width = ?, height = ?, style = ?, fps = ?, notes = ?, updated_at = ? WHERE id = ?');
    $st->execute([
        trim((string) ($data['name'] ?? '')) ?: 'Untitled project',
        max(256, (int) ($data['width'] ?? 1280)),
        max(256, (int) ($data['height'] ?? 768)),
        trim((string) ($data['style'] ?? '')) === '' ? null : trim((string) $data['style']),
        max(1, (int) ($data['fps'] ?? 24)),
        trim((string) ($data['notes'] ?? '')) ?: null,
        now(),
        $id,
    ]);
}

function project_touch(int $id): void
{
    db()->prepare('UPDATE projects SET updated_at = ? WHERE id = ?')->execute([now(), $id]);
}

function project_delete(int $id): void
{
    db()->prepare('DELETE FROM projects WHERE id = ?')->execute([$id]);
}

/* -------------------------------------------------------------------- assets */

function assets_for(int $projectId, ?string $kind = null): array
{
    $sql = 'SELECT a.*, (SELECT COUNT(*) FROM images i WHERE i.asset_id = a.id) AS image_count
            FROM assets a WHERE a.project_id = ?';
    $args = [$projectId];
    if ($kind !== null) {
        $sql .= ' AND a.kind = ?';
        $args[] = $kind;
    }
    $sql .= ' ORDER BY a.kind, a.position, a.id';
    $st = db()->prepare($sql);
    $st->execute($args);
    return $st->fetchAll();
}

function asset_get(int $id): ?array
{
    $st = db()->prepare('SELECT * FROM assets WHERE id = ?');
    $st->execute([$id]);
    return $st->fetch() ?: null;
}

function asset_create(int $projectId, string $kind, string $name, ?string $description = null): int
{
    $kind = array_key_exists($kind, ASSET_KINDS) ? $kind : 'character';
    $next = db()->prepare('SELECT COALESCE(MAX(position), 0) + 1 FROM assets WHERE project_id = ? AND kind = ?');
    $next->execute([$projectId, $kind]);
    $pos = (int) $next->fetchColumn();
    $st = db()->prepare('INSERT INTO assets(project_id, kind, name, description, notes, position, created_at, updated_at)
                         VALUES(?,?,?,?,?,?,?,?)');
    $st->execute([$projectId, $kind, trim($name) ?: ucfirst($kind), $description, null, $pos, now(), now()]);
    project_touch($projectId);
    return (int) db()->lastInsertId();
}

function asset_update(int $id, array $data): void
{
    $asset = asset_get($id);
    if ($asset === null) {
        return;
    }
    $st = db()->prepare('UPDATE assets SET name = ?, description = ?, notes = ?, kind = ?, updated_at = ? WHERE id = ?');
    $st->execute([
        trim((string) ($data['name'] ?? '')) ?: $asset['name'],
        trim((string) ($data['description'] ?? '')) ?: null,
        trim((string) ($data['notes'] ?? '')) ?: null,
        array_key_exists((string) ($data['kind'] ?? ''), ASSET_KINDS) ? $data['kind'] : $asset['kind'],
        now(),
        $id,
    ]);
    project_touch((int) $asset['project_id']);
}

function asset_delete(int $id): void
{
    $asset = asset_get($id);
    if ($asset === null) {
        return;
    }
    db()->prepare('DELETE FROM images WHERE asset_id = ?')->execute([$id]);
    db()->prepare('DELETE FROM assets WHERE id = ?')->execute([$id]);
    project_touch((int) $asset['project_id']);
}

/* -------------------------------------------------------------------- images */

function images_for(int $assetId): array
{
    $st = db()->prepare('SELECT * FROM images WHERE asset_id = ? ORDER BY position, id');
    $st->execute([$assetId]);
    return $st->fetchAll();
}

function image_get(int $id): ?array
{
    $st = db()->prepare('SELECT i.*, a.name AS asset_name, a.kind AS asset_kind, a.description AS asset_description,
                                a.project_id
                         FROM images i JOIN assets a ON a.id = i.asset_id WHERE i.id = ?');
    $st->execute([$id]);
    return $st->fetch() ?: null;
}

function image_add(int $assetId, string $role, array $stored, int $w, int $h, bool $alpha): int
{
    $next = db()->prepare('SELECT COALESCE(MAX(position), 0) + 1 FROM images WHERE asset_id = ?');
    $next->execute([$assetId]);
    $pos = (int) $next->fetchColumn();
    $st = db()->prepare('INSERT INTO images(asset_id, role, file, mime, width, height, bytes, sha256, has_alpha, position, created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)');
    $st->execute([$assetId, $role, $stored['file'], $stored['mime'], $w, $h, $stored['bytes'], $stored['sha256'], $alpha ? 1 : 0, $pos, now()]);
    $asset = asset_get($assetId);
    if ($asset !== null) {
        project_touch((int) $asset['project_id']);
    }
    return (int) db()->lastInsertId();
}

function image_delete(int $id): void
{
    $img = image_get($id);
    if ($img === null) {
        return;
    }
    db()->prepare('DELETE FROM images WHERE id = ?')->execute([$id]);
    project_touch((int) $img['project_id']);
}

function image_set_role(int $id, string $role): void
{
    if (!array_key_exists($role, IMAGE_ROLES)) {
        return;
    }
    db()->prepare('UPDATE images SET role = ? WHERE id = ?')->execute([$role, $id]);
}

/* --------------------------------------------------------------------- shots */

function shots_for(int $projectId): array
{
    $st = db()->prepare('SELECT s.*,
            (SELECT COUNT(*) FROM shot_assets sa WHERE sa.shot_id = s.id) AS asset_count,
            (SELECT COUNT(*) FROM bookends b WHERE b.shot_id = s.id) AS bookend_count
        FROM shots s WHERE s.project_id = ? ORDER BY s.position, s.id');
    $st->execute([$projectId]);
    return $st->fetchAll();
}

function shot_get(int $id): ?array
{
    $st = db()->prepare('SELECT * FROM shots WHERE id = ?');
    $st->execute([$id]);
    return $st->fetch() ?: null;
}

function shot_create(int $projectId, string $slug = ''): int
{
    $next = db()->prepare('SELECT COALESCE(MAX(position), 0) + 1 FROM shots WHERE project_id = ?');
    $next->execute([$projectId]);
    $pos = (int) $next->fetchColumn();
    if ($slug === '') {
        $slug = sprintf('shot_%03d', $pos);
    }
    $st = db()->prepare('INSERT INTO shots(project_id, slug, position, created_at, updated_at) VALUES(?,?,?,?,?)');
    $st->execute([$projectId, $slug, $pos, now(), now()]);
    project_touch($projectId);
    return (int) db()->lastInsertId();
}

/**
 * Partial update: keys that are absent keep their current value. The review
 * decision, the shot form and the duplicate action all call this with different
 * subsets, and a missing 'reviewed' key must not wipe the column.
 */
function shot_update(int $id, array $data): void
{
    $shot = shot_get($id);
    if ($shot === null) {
        return;
    }
    $pick = static function (string $key) use ($data, $shot): string {
        return array_key_exists($key, $data)
            ? trim((string) $data[$key])
            : (string) ($shot[$key] ?? '');
    };

    $reviewed = (string) ($data['reviewed'] ?? $shot['reviewed']);
    if (!in_array($reviewed, ['pending', 'approved', 'rejected'], true)) {
        $reviewed = (string) $shot['reviewed'];
    }

    $st = db()->prepare('UPDATE shots SET slug = ?, start_text = ?, stop_text = ?, motion_prompt = ?,
                         duration_s = ?, reviewed = ?, review_note = ?, updated_at = ? WHERE id = ?');
    $st->execute([
        $pick('slug') ?: (string) $shot['slug'],
        $pick('start_text'),
        $pick('stop_text'),
        $pick('motion_prompt'),
        array_key_exists('duration_s', $data) ? max(0.0, (float) $data['duration_s']) : (float) $shot['duration_s'],
        $reviewed,
        array_key_exists('review_note', $data) ? ($pick('review_note') ?: null) : $shot['review_note'],
        now(),
        $id,
    ]);
    project_touch((int) $shot['project_id']);
}

function shot_delete(int $id): void
{
    $shot = shot_get($id);
    if ($shot === null) {
        return;
    }
    db()->prepare('DELETE FROM bookends WHERE shot_id = ?')->execute([$id]);
    db()->prepare('DELETE FROM shot_assets WHERE shot_id = ?')->execute([$id]);
    db()->prepare('DELETE FROM shots WHERE id = ?')->execute([$id]);
    project_touch((int) $shot['project_id']);
}

function shot_duplicate(int $id): int
{
    $shot = shot_get($id);
    if ($shot === null) {
        return 0;
    }
    $newId = shot_create((int) $shot['project_id']);
    shot_update($newId, [
        'slug' => $shot['slug'] . '_copy',
        'start_text' => $shot['start_text'],
        'stop_text' => $shot['stop_text'],
        'motion_prompt' => $shot['motion_prompt'],
        'duration_s' => $shot['duration_s'],
    ]);
    $st = db()->prepare('INSERT INTO shot_assets(shot_id, asset_id, position) VALUES(?,?,?)');
    foreach (shot_selection($id) as $i => $assetId) {
        $st->execute([$newId, $assetId, $i]);
    }
    return $newId;
}

/** @return list<int> asset ids in slot order */
function shot_selection(int $shotId): array
{
    $st = db()->prepare('SELECT asset_id FROM shot_assets WHERE shot_id = ? ORDER BY position, asset_id');
    $st->execute([$shotId]);
    return array_map('intval', $st->fetchAll(PDO::FETCH_COLUMN));
}

/** @param list<int> $assetIds */
function shot_set_selection(int $shotId, array $assetIds): void
{
    $pdo = db();
    $pdo->beginTransaction();
    try {
        $pdo->prepare('DELETE FROM shot_assets WHERE shot_id = ?')->execute([$shotId]);
        $ins = $pdo->prepare('INSERT OR IGNORE INTO shot_assets(shot_id, asset_id, position) VALUES(?,?,?)');
        $i = 0;
        foreach ($assetIds as $aid) {
            if ($aid > 0) {
                $ins->execute([$shotId, $aid, $i++]);
            }
        }
        $pdo->commit();
    } catch (Throwable $e) {
        $pdo->rollBack();
        throw $e;
    }
}

function shot_audio_set(int $shotId, array $stored, ?float $seconds): void
{
    db()->prepare('UPDATE shots SET audio_file = ?, audio_mime = ?, audio_bytes = ?, audio_seconds = ?, updated_at = ? WHERE id = ?')
        ->execute([$stored['file'], $stored['mime'], $stored['bytes'], $seconds, now(), $shotId]);
}

function shot_audio_clear(int $shotId): void
{
    db()->prepare('UPDATE shots SET audio_file = NULL, audio_mime = NULL, audio_bytes = 0, audio_seconds = NULL WHERE id = ?')
        ->execute([$shotId]);
}

/* ------------------------------------------------------------------ bookends */

/** @return array{start:?array, stop:?array} */
function shot_bookends(int $shotId): array
{
    $st = db()->prepare('SELECT * FROM bookends WHERE shot_id = ? ORDER BY uploaded_at DESC');
    $st->execute([$shotId]);
    $out = ['start' => null, 'stop' => null];
    foreach ($st->fetchAll() as $row) {
        if (!isset($out[$row['tag']]) || $out[$row['tag']] === null) {
            $out[$row['tag']] = $row;
        }
    }
    return $out;
}

function bookend_set(int $shotId, string $tag, array $stored, int $w, int $h): void
{
    $tag = $tag === 'stop' ? 'stop' : 'start';
    db()->prepare('DELETE FROM bookends WHERE shot_id = ? AND tag = ?')->execute([$shotId, $tag]);
    db()->prepare('INSERT INTO bookends(shot_id, tag, file, mime, width, height, bytes, uploaded_at) VALUES(?,?,?,?,?,?,?,?)')
        ->execute([$shotId, $tag, $stored['file'], $stored['mime'], $w, $h, $stored['bytes'], now()]);
}

function bookend_delete(int $shotId, string $tag): void
{
    db()->prepare('DELETE FROM bookends WHERE shot_id = ? AND tag = ?')->execute([$shotId, $tag]);
}

/** Shots that have both frames, for the review screen. */
function shots_with_bookends(int $projectId): array
{
    $st = db()->prepare('SELECT s.*,
            (SELECT COUNT(*) FROM bookends b WHERE b.shot_id = s.id AND b.tag = \'start\') AS has_start,
            (SELECT COUNT(*) FROM bookends b WHERE b.shot_id = s.id AND b.tag = \'stop\') AS has_stop
        FROM shots s WHERE s.project_id = ? ORDER BY s.position, s.id');
    $st->execute([$projectId]);
    return $st->fetchAll();
}
