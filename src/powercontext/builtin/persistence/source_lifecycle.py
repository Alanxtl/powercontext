# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generation eligibility predicates that leave immutable Topic evidence intact."""

from typing import Literal

from sqlalchemy import ColumnElement, select

from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE


def topic_available(scope_id: str, artifact_id: ColumnElement[str]) -> ColumnElement[bool]:
    nodes = SOURCE_LIFECYCLE_NODES_TABLE.alias()
    return (
        ~select(nodes.c.node_id)
        .where(
            nodes.c.scope_id == scope_id,
            nodes.c.kind == "topic",
            nodes.c.artifact_id == artifact_id,
            nodes.c.available.is_(False),
        )
        .correlate_except(nodes)
        .exists()
    )


def topic_available_sql(
    alias: Literal[
        "v",
        "pc_topic_memory_topic_fts",
        "pc_topic_memory_chunk_fts",
        "pc_topic_memory_vector_topics",
        "pc_topic_memory_vector_chunks",
    ],
) -> str:
    """Use only internal SQL aliases; caller values remain bound parameters."""
    return """NOT EXISTS (
        SELECT 1 FROM pc_source_lifecycle_nodes AS lifecycle
        WHERE lifecycle.scope_id = target.scope_id
          AND lifecycle.artifact_id = target.artifact_id
          AND lifecycle.kind = 'topic' AND lifecycle.available = 0
    )""".replace("target.", alias + ".")
