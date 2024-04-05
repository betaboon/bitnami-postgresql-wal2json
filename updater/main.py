from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import textwrap
from datetime import datetime, timezone

import requests
from loguru import logger
from pydantic.dataclasses import dataclass
from pydantic.json import pydantic_encoder

DEFAULT_IMAGE_NAME = "betaboon/bitnami-postgresql-wal2json"

POSTGRESQL_URL = "https://registry.hub.docker.com/v2/repositories/bitnami/postgresql/tags/?page_size=50"

POSTGRESQL_TAG_REGEX = (
    "(?P<postgresql_version>[^-]+)-debian-(?P<debian_version>[^-]+)-.*"
)


@dataclass
class Wal2JsonRecord:
    last_updated: datetime
    version: str
    upstream_tag: str


@dataclass
class PostgresqlRecord:
    last_updated: datetime
    version: str
    major_version: str
    upstream_tag: str
    tags: list[str] = dataclasses.field(default_factory=list)

    @staticmethod
    def parse_tag(tag: str) -> tuple[str, str]:
        match = re.match(POSTGRESQL_TAG_REGEX, tag)
        groups = match.groupdict()
        return groups["postgresql_version"], groups["debian_version"]

    @classmethod
    def from_version(
        cls,
        update_time: datetime,
        image_name: str,
        major_version: str,
        upstream_tag: str,
        latest: bool = False,
    ) -> PostgresqlRecord:
        postgresql_version, debian_version = cls.parse_tag(upstream_tag)
        record = PostgresqlRecord(
            last_updated=update_time,
            version=postgresql_version,
            major_version=major_version,
            upstream_tag=upstream_tag,
        )
        record.tags.append(f"{image_name}:{upstream_tag}")
        record.tags.append(f"{image_name}:{major_version}-debian-{debian_version}")
        record.tags.append(f"{image_name}:{postgresql_version}")
        record.tags.append(f"{image_name}:{major_version}")
        if latest:
            record.tags.append(f"{image_name}:latest")
        return record


@dataclass
class Manifest:
    wal2json: Wal2JsonRecord
    postgresql: list[PostgresqlRecord] = dataclasses.field(default_factory=list)


def get_wal2json_record(update_time: datetime) -> Wal2JsonRecord:
    return Wal2JsonRecord(
        last_updated=update_time,
        version="2.5",
        upstream_tag="wal2json_2_5",
    )


def get_postgresql_records(
    update_time: datetime,
    image_name: str,
    postgresql_major_versions: list[str],
) -> list[PostgresqlRecord]:
    records = []
    response = requests.get(POSTGRESQL_URL)
    tags = response.json().get("results")
    for i, major_version in enumerate(postgresql_major_versions):
        version_tags = [t for t in tags if t["name"].startswith(f"{major_version}.")]
        version_tags_sorted = sorted(version_tags, key=lambda t: t["last_updated"])
        version_tag_latest = version_tags_sorted[-1]
        record = PostgresqlRecord.from_version(
            update_time=update_time,
            image_name=image_name,
            major_version=major_version,
            upstream_tag=version_tag_latest["name"],
            latest=(i + 1 == len(postgresql_major_versions)),
        )
        logger.debug(f"Found postgresql tag: {record.upstream_tag}")
        records.append(record)
    return records


def generate_manifest(
    update_time: datetime,
    image_name: str,
    postgresql_major_versions: list[str],
) -> Manifest:
    wal2json_record = get_wal2json_record(update_time=update_time)

    postgresql_records = get_postgresql_records(
        update_time=update_time,
        image_name=image_name,
        postgresql_major_versions=postgresql_major_versions,
    )

    manifest = Manifest(
        wal2json=wal2json_record,
        postgresql=postgresql_records,
    )
    return manifest


def update_manifest(
    manifest: Manifest | None,
    update_time: datetime,
    image_name: str,
    postgresql_major_versions: list[str],
) -> Manifest:
    postgresql_major_versions = sorted(postgresql_major_versions)
    logger.debug(f"Updating manifest for majors: {' '.join(postgresql_major_versions)}")
    new_manifest = generate_manifest(
        update_time=update_time,
        image_name=image_name,
        postgresql_major_versions=postgresql_major_versions,
    )

    if manifest is None:
        return new_manifest

    if manifest.wal2json.upstream_tag != new_manifest.wal2json.upstream_tag:
        manifest.wal2json = new_manifest.wal2json

    old_postgresql_records = {r.major_version: r for r in manifest.postgresql}
    new_postgresql_records = {r.major_version: r for r in new_manifest.postgresql}

    postgresql_records = []
    for postgresql_major_version in postgresql_major_versions:
        old_postgresql_record = old_postgresql_records.get(postgresql_major_version)
        new_postgresql_record = new_postgresql_records.get(postgresql_major_version)

        if not old_postgresql_record and new_postgresql_record:
            postgresql_records.append(new_postgresql_record)
        elif (
            old_postgresql_record
            and new_postgresql_record
            and new_postgresql_record.upstream_tag != old_postgresql_record.upstream_tag
        ):
            postgresql_records.append(new_postgresql_record)
        elif old_postgresql_record:
            postgresql_records.append(old_postgresql_record)
        else:
            continue

    manifest.postgresql = postgresql_records

    return manifest


def load_manifest(manifest_file: pathlib.Path) -> Manifest:
    return Manifest.__pydantic_model__.parse_file(manifest_file)


def save_manifest(manifest_file: pathlib.Path, manifest: Manifest) -> None:
    with manifest_file.open(mode="w") as out:
        json.dump(manifest, out, indent=4, default=pydantic_encoder)


def save_summary(
    summary_file: pathlib.Path,
    manifest: Manifest,
    update_time: datetime,
) -> None:
    def bool_emoji(value: bool) -> str:
        return ":heavy_check_mark:" if value else ":x:"

    def wal2json_summary(record: Wal2JsonRecord) -> str:
        summary = f"""
            # wal2json

            Updated: {bool_emoji(record.last_updated == update_time)}
            Tag: {record.upstream_tag}
            Version: {record.version}
        """
        return textwrap.dedent(summary)

    def postgresql_summary(record: PostgresqlRecord) -> str:
        summary = f"""
            # postgresql {record.major_version}

            Updated: {bool_emoji(record.last_updated == update_time)}
            Tag: {record.upstream_tag}
            Version: {record.version}
        """
        return textwrap.dedent(summary)

    summary = wal2json_summary(manifest.wal2json)
    for postgresql_record in manifest.postgresql:
        summary += postgresql_summary(postgresql_record)

    with open(summary_file, mode="w") as out:
        out.write(summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--image-name", type=str, default=DEFAULT_IMAGE_NAME)
    parser.add_argument("--update-time", type=datetime.fromisoformat)
    parser.add_argument("--summary", type=pathlib.Path)
    parser.add_argument("postgresql_major_versions", type=str, nargs="+")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    manifest = None
    update_time = args.update_time

    if not update_time:
        update_time = datetime.now(tz=timezone.utc).replace(microsecond=0)

    if args.manifest.is_file():
        manifest = load_manifest(manifest_file=args.manifest)

    updated_manifest = update_manifest(
        manifest=manifest,
        update_time=update_time,
        image_name=args.image_name,
        postgresql_major_versions=args.postgresql_major_versions,
    )

    save_manifest(manifest_file=args.manifest, manifest=updated_manifest)

    if args.summary:
        save_summary(
            summary_file=args.summary,
            manifest=updated_manifest,
            update_time=update_time,
        )


if __name__ == "__main__":
    main()
