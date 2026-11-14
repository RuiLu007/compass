"""Identity check for previously configured tenant-bound data accounts."""

from dataclasses import dataclass

from compass_agent.data_connections.transport import DatabaseIdentityMismatch

RETAIL_IDENTITY_SQL = """
SELECT
    session_user,
    current_user,
    current_setting('transaction_read_only'),
    (
        SELECT bool_and(
            row_security_active(relation::regclass)
            AND NOT has_table_privilege(relation, 'INSERT, UPDATE, DELETE, TRUNCATE')
            AND NOT has_any_column_privilege(relation, 'INSERT, UPDATE')
        )
        FROM (VALUES ('core.channel_product_daily'), ('core.channel'), ('core.sku'), ('core.spu'))
             AS required(relation)
    ),
    EXISTS (SELECT FROM core.channel WHERE tenant_id = %(tenant)s)
"""


@dataclass(frozen=True, slots=True)
class LegacyScopeCheck:
    tenant_id: int
    role: str

    async def __call__(self, cursor):
        await cursor.execute(RETAIL_IDENTITY_SQL, {"tenant": self.tenant_id})
        if await cursor.fetchall() != [(self.role, self.role, "on", True, True)]:
            raise DatabaseIdentityMismatch("configured data scope mismatch")
