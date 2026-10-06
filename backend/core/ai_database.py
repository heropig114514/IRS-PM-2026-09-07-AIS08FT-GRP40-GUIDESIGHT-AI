"""Responsibilities: Provide introspection, ORM CRUD and SQL for MCP database tools.
Implementation: Use Django's configured connection; mutations use one atomic transaction.
Related Modules: core.ai_mcp; config.settings/production/camera_server select the database.
Declaration Index: encode — JSON-safe values; database_schema — table/model introspection;
model_for — resolve app.Model; query_rows — ordered ORM pagination;
write_rows — validated atomic CRUD; execute_sql — atomic SQL and bounded result fetch.
Variable Index: None.
"""

import base64
import json

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction


def encode(value):
    """Functionality: Normalize database values for MCP structured results.
    Inputs: value (rows/scalars). Outputs: JSON-safe copy, including dates, UUID and decimal.
    Logic: Recursively encode bytes as tagged base64; use Django's scalar JSON conversion.
    Constraints: Decimal values retain precision as strings; no database writes.
    """
    if isinstance(value, bytes | memoryview):
        return {"encoding": "base64", "data": base64.b64encode(value).decode()}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [encode(item) for item in value]
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def database_schema():
    """Functionality: Describe physical tables and available Django models.
    Inputs: Configured default connection and app registry. Outputs: Vendor, tables and models.
    Logic: Introspect actual tables and map concrete ORM fields to types and nullability.
    Constraints: No credentials or row values; many-to-many joins are accessible through SQL.
    """
    with connection.cursor() as cursor:
        tables = [
            {
                "name": table,
                "columns": [
                    {
                        "name": column.name,
                        "nullable": column.null_ok,
                        "type_code": str(column.type_code),
                    }
                    for column in connection.introspection.get_table_description(cursor, table)
                ],
            }
            for table in connection.introspection.table_names(cursor)
        ]
    models = [
        {
            "model": model._meta.label,
            "table": model._meta.db_table,
            "fields": [
                {
                    "name": field.name,
                    "attname": field.attname,
                    "type": field.get_internal_type(),
                    "nullable": field.null,
                    "primary_key": field.primary_key,
                    "editable": field.editable,
                    "relation": field.related_model._meta.label if field.is_relation else None,
                }
                for field in model._meta.concrete_fields
            ],
        }
        for model in apps.get_models()
    ]
    return {"vendor": connection.vendor, "tables": tables, "models": models}


def model_for(label):
    """Functionality: Resolve an explicit app.Model identifier.
    Inputs: label string. Outputs: Registered model class or ValueError.
    Logic: Use Django's registry and preserve its model identity.
    Constraints: Imported/inherited symbols do not create additional registered models.
    """
    try:
        model = apps.get_model(label)
    except (LookupError, ValueError) as error:
        raise ValueError("Unknown model; use database_schema to obtain app.Model labels") from error
    if model is None:
        raise ValueError("Unknown model")
    return model


def query_rows(model, filters, fields, order_by, limit, offset):
    """Functionality: Read an ordered page of ORM rows using Django lookup filters.
    Inputs: model label, filters dict, optional fields, order_by list, limit and offset.
    Outputs: Total matching count, rows and explicit pagination metadata.
    Logic: Default ordering is the primary key; evaluate only the requested slice.
    Constraints: 1 <= limit <= 1000 and offset >= 0; no implicit filtering or persistence.
    """
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("limit must be 1..1000 and offset must be nonnegative")
    cls = model_for(model)
    rows = cls.objects.filter(**filters).order_by(*(order_by or [cls._meta.pk.name]))
    names = fields if fields is not None else [field.attname for field in cls._meta.concrete_fields]
    return {
        "model": cls._meta.label,
        "count": rows.count(),
        "limit": limit,
        "offset": offset,
        "rows": encode(list(rows.values(*names)[offset : offset + limit])),
    }


def write_rows(action, model, values, filters):
    """Functionality: Create, update or delete ORM objects with observable counts.
    Inputs: action, model label, values dict, filters dict (required for update/delete).
    Outputs: Created primary key, updated count or Django cascade deletion counts.
    Logic: Validate writable concrete field names; full_clean/save per object inside atomic.
    Constraints: Explicit {} filters match all rows; raw passwords are not automatically hashed.
    Delete uses Django cascades; exceptions roll back the complete operation, without retries.
    """
    cls = model_for(model)
    allowed = {field.attname for field in cls._meta.concrete_fields}
    if set(values) - allowed:
        raise ValueError(
            "Unknown field; foreign keys use the schema's attname (for example user_id)"
        )
    with transaction.atomic():
        if action == "create":
            if filters is not None:
                raise ValueError("create does not accept filters")
            obj = cls(**values)
            obj.full_clean()
            obj.save(force_insert=True)
            return {"created": 1, "primary_key": encode(obj.pk)}
        if filters is None:
            raise ValueError("update/delete require explicit filters; {} matches all rows")
        rows = cls.objects.select_for_update().filter(**filters)
        if action == "delete":
            if values:
                raise ValueError("delete does not accept values")
            count, cascade = rows.delete()
            return {"deleted": count, "cascade": cascade}
        if action == "update":
            if cls._meta.pk.attname in values:
                raise ValueError(
                    "ORM update cannot change primary keys; use database_sql explicitly"
                )
            count = 0
            for obj in rows.iterator():
                for name, value in values.items():
                    setattr(obj, name, value)
                obj.full_clean()
                obj.save()
                count += 1
            return {"updated": count}
        raise ValueError("action must be create, update or delete")


def execute_sql(sql, parameters, limit):
    """Functionality: Execute a caller-supplied SQL statement, including writes and DDL.
    Inputs: sql string, bound parameters list, result limit. Outputs: Columns, rows and rowcount.
    Logic: Execute once in atomic; fetch limit+1 to report truncation explicitly.
    Constraints: Use Django %s placeholders; one statement; no SQL permission filter or retries.
    Returned row limits do not limit the number of rows modified by SQL.
    """
    if not sql.strip() or not 1 <= limit <= 1000:
        raise ValueError("sql is required and limit must be 1..1000")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        columns = [column[0] for column in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(limit + 1) if columns else []
        return {
            "columns": columns,
            "rows": encode(rows[:limit]),
            "rowcount": cursor.rowcount,
            "truncated": len(rows) > limit,
        }
