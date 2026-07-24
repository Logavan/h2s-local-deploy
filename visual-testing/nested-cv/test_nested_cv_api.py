#!/usr/bin/env python3
"""Nested CV Flattener - API tests"""
import io, json, time, sys, os

try:
    import requests
except ImportError:
    print("[FAIL] requests not installed. Run: pip install requests")
    sys.exit(1)

BASE = "http://localhost:8080"


def ok(result, msg):
    icon = "[PASS]" if result else "[FAIL]"
    print("  " + icon + " " + msg)
    return result


def j(r):
    try:
        return True, r.json()
    except Exception as e:
        return False, str(e)


def cv_payload(name, version=2, deps=None, schema=None, chunks=None):
    d = {
        "cv_name": name,
        "sql_chunks": chunks or [{"sql_content": "SELECT 1", "chunk_id": "c1"}],
        "format_version": version,
    }
    if deps is not None:
        d["dependencies"] = deps
    if schema is not None:
        d["output_schema"] = schema
    return json.dumps(d)


def sess(dialect="bigquery", fmt="sql"):
    resp = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": dialect, "output_format": fmt})
    data = resp.json()
    return data.get("session", {}).get("session_id")


def cleanup_all():
    for i in range(5):
        try:
            sid = sess()
            if sid:
                requests.delete(BASE + "/api/nested/sessions/" + sid)
        except Exception:
            pass


# ----------------------------------------------------------------
# T01 - health
# ----------------------------------------------------------------
def t01():
    r = requests.get(BASE + "/health")
    ok_, data = j(r)
    return ok_ and ok(data.get("status") == "healthy", "health check")

# ----------------------------------------------------------------
# T02 - create session SQL
# ----------------------------------------------------------------
def t02():
    sid = sess("bigquery", "sql")
    r = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": "bigquery", "output_format": "sql"})
    ok_, data = j(r)
    if not ok_:
        return ok(False, "create session exception: " + data)
    s = data.get("session", {})
    r1 = ok(bool(s.get("session_id")), "session_id present")
    r2 = ok(s.get("target_dialect") == "bigquery", "dialect bigquery")
    r3 = ok(s.get("output_format") == "sql", "format sql")
    r4 = ok(isinstance(s.get("artifacts"), dict), "artifacts is dict")
    r5 = ok(len(s.get("artifacts", {})) == 0, "artifacts empty")
    return r1 and r2 and r3 and r4 and r5

# ----------------------------------------------------------------
# T03 - create session PySpark
# ----------------------------------------------------------------
def t03():
    sid = sess("databricks", "pyspark")
    r = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": "databricks", "output_format": "pyspark"})
    ok_, data = j(r)
    if not ok_:
        return ok(False, "pyspark session exception: " + data)
    s = data.get("session", {})
    r1 = ok(bool(s.get("session_id")), "pyspark session_id present")
    r2 = ok(s.get("output_format") == "pyspark", "output_format pyspark")
    return r1 and r2

# ----------------------------------------------------------------
# T04 - invalid format
# ----------------------------------------------------------------
def t04():
    r = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": "bigquery", "output_format": "csv"})
    return ok(r.status_code == 400, "invalid format rejected: " + str(r.status_code))

# ----------------------------------------------------------------
# T05 - get session
# ----------------------------------------------------------------
def t05():
    sid = sess()
    r = requests.get(BASE + "/api/nested/sessions/" + sid)
    ok_, data = j(r)
    if not ok_:
        return ok(False, "get session exception: " + data)
    r1 = ok(data.get("success") is True, "success=True")
    r2 = ok(data.get("session", {}).get("session_id") == sid, "sid matches")
    return r1 and r2

# ----------------------------------------------------------------
# T06 - fake session
# ----------------------------------------------------------------
def t06():
    r = requests.get(BASE + "/api/nested/sessions/fake-id-99999")
    return ok(r.status_code == 404, "fake session 404: " + str(r.status_code))

# ----------------------------------------------------------------
# T07 - add CV simple
# ----------------------------------------------------------------
def t07():
    sid = sess()
    payload = {"file_content": cv_payload("TEST_CV"), "file_name": "test.xlsx"}
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json=payload)
    ok_, data = j(r)
    if not ok_:
        return ok(False, "add CV exception: " + data)
    a = data.get("artifact", {})
    r1 = ok(data.get("success") is True, "success=True")
    r2 = ok(bool(a.get("artifact_id")), "artifact_id present")
    r3 = ok(a.get("cv_display_name") == "TEST_CV", "cv_display_name=TEST_CV")
    r4 = ok(a.get("emission_mode") == "inline_cte", "emission_mode inline_cte")
    r5 = ok(a.get("target_view_name") == "TGT_TEST_CV", "target_view=TGT_TEST_CV")
    return r1 and r2 and r3 and r4 and r5

# ----------------------------------------------------------------
# T08 - add CV with full metadata
# ----------------------------------------------------------------
def t08():
    sid = sess()
    deps = [{"source_ref_raw": "CHILD", "source_ref_canonical": "CHILD", "object_kind": "calculation_view", "referenced_by_node": "n1", "required_columns": ["c1"]}]
    schema = [{"ordinal": 1, "column_name": "id", "data_type": "INTEGER"}, {"ordinal": 2, "column_name": "name", "data_type": "VARCHAR"}]
    payload = {"file_content": cv_payload("PARENT", deps=deps, schema=schema), "file_name": "parent.xlsx"}
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json=payload)
    ok_, data = j(r)
    if not ok_:
        return ok(False, "add full metadata CV exception: " + data)
    a = data.get("artifact", {})
    r1 = ok(len(a.get("dependencies", [])) == 1, "1 dependency")
    r2 = ok(len(a.get("output_schema", [])) == 2, "2 output columns")
    r3 = ok(a.get("output_schema", [{}])[0].get("column_name") == "id", "first col name=id")
    r4 = ok(len(a.get("warnings", [])) == 0, "no v2 warnings")
    return r1 and r2 and r3 and r4

# ----------------------------------------------------------------
# T09 - legacy v1 warning
# ----------------------------------------------------------------
def t09():
    sid = sess()
    payload = {"file_content": cv_payload("LEGACY", version=1), "file_name": "legacy.xlsx"}
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json=payload)
    ok_, data = j(r)
    if not ok_:
        return ok(False, "legacy CV exception: " + data)
    a = data.get("artifact", {})
    r1 = ok(a.get("format_version") == 1, "format_version=1")
    codes = [w.get("code") for w in a.get("warnings", [])]
    r2 = ok("LEGACY_FORMAT" in codes, "LEGACY_FORMAT warning present")
    return r1 and r2

# ----------------------------------------------------------------
# T10 - non-JSON graceful fallback
# ----------------------------------------------------------------
def t10():
    sid = sess()
    payload = {"file_content": "not json at all!!!", "file_name": "bad.xlsx"}
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json=payload)
    ok_, data = j(r)
    if not ok_:
        return ok(False, "non-JSON exception: " + data)
    a = data.get("artifact", {})
    r1 = ok(data.get("success") is True, "accepted with fallback")
    r2 = ok(a.get("cv_display_name") == "BAD", "display_name from filename")
    return r1 and r2

# ----------------------------------------------------------------
# T11 - update CV emission mode and target name
# ----------------------------------------------------------------
def t11():
    sid = sess()
    add_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("UPDCV"), "file_name": "upd.xlsx"}).json()
    aid = add_resp.get("artifact", {}).get("artifact_id")
    patch_resp = requests.patch(BASE + "/api/nested/sessions/" + sid + "/cvs/" + aid,
        json={"emission_mode": "emit_view", "target_view_name": "CUSTOM_VIEW"})
    ok_, data = j(patch_resp)
    if not ok_:
        return ok(False, "patch exception: " + data)
    a = data.get("artifact", {})
    r1 = ok(a.get("emission_mode") == "emit_view", "emission_mode=emit_view")
    r2 = ok(a.get("target_view_name") == "CUSTOM_VIEW", "target_view=CUSTOM_VIEW")
    return r1 and r2

# ----------------------------------------------------------------
# T12 - delete CV
# ----------------------------------------------------------------
def t12():
    sid = sess()
    add_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("DELCV"), "file_name": "del.xlsx"}).json()
    aid = add_resp.get("artifact", {}).get("artifact_id")
    requests.delete(BASE + "/api/nested/sessions/" + sid + "/cvs/" + aid)
    get_resp = requests.get(BASE + "/api/nested/sessions/" + sid).json()
    artifacts = get_resp.get("session", {}).get("artifacts", {})
    return ok(aid not in artifacts, "CV removed from session")

# ----------------------------------------------------------------
# T13 - resolve/save dependency links
# ----------------------------------------------------------------
def t13():
    sid = sess()
    c1_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("CHILD"), "file_name": "c1.xlsx"}).json()
    p1_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("PARENT"), "file_name": "p1.xlsx"}).json()
    c1id = c1_resp.get("artifact", {}).get("artifact_id")
    p1id = p1_resp.get("artifact", {}).get("artifact_id")
    links_payload = {"links": [{"consumer_artifact_id": p1id, "source_ref_canonical": "CHILD", "resolution": "uploaded_cv", "producer_artifact_id": c1id}]}
    r = requests.put(BASE + "/api/nested/sessions/" + sid + "/links", json=links_payload)
    ok_, data = j(r)
    if not ok_:
        return ok(False, "resolve links exception: " + data)
    return ok(data.get("success") is True, "links saved successfully")

# ----------------------------------------------------------------
# T14 - validate empty session
# ----------------------------------------------------------------
def t14():
    sid = sess()
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/validate")
    ok_, data = j(r)
    if not ok_:
        return ok(False, "validate empty exception: " + data)
    r1 = ok(data.get("valid") is False, "empty session invalid")
    codes = [e.get("code") for e in data.get("errors", [])]
    r2 = ok("NO_ARTIFACTS" in codes, "NO_ARTIFACTS error present")
    return r1 and r2

# ----------------------------------------------------------------
# T15 - validate single CV (no cycle)
# ----------------------------------------------------------------
def t15():
    sid = sess()
    add_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("SOLO"), "file_name": "solo.xlsx"}).json()
    aid = add_resp.get("artifact", {}).get("artifact_id")
    requests.patch(BASE + "/api/nested/sessions/" + sid + "/cvs/" + aid,
        json={"emission_mode": "emit_view", "target_view_name": "SOLO_OUT"})
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/validate")
    ok_, data = j(r)
    if not ok_:
        return ok(False, "validate exception: " + data)
    gs = data.get("graph_summary", {})
    r1 = ok(data.get("valid") is True, "solo CV valid")
    r2 = ok(len(data.get("errors", [])) == 0, "no validation errors")
    r3 = ok(len(gs.get("nodes", [])) == 1, "1 graph node")
    r4 = ok(gs.get("has_cycles") is False, "no cycles")
    return r1 and r2 and r3 and r4

# ----------------------------------------------------------------
# T16 - cycle detection
# ----------------------------------------------------------------
def t16():
    sid = sess()
    c1_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("N1"), "file_name": "n1.xlsx"}).json()
    p1_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("N2"), "file_name": "n2.xlsx"}).json()
    c1id = c1_resp.get("artifact", {}).get("artifact_id")
    p1id = p1_resp.get("artifact", {}).get("artifact_id")
    links_payload = {"links": [
        {"consumer_artifact_id": p1id, "source_ref_canonical": "N1", "resolution": "uploaded_cv", "producer_artifact_id": c1id},
        {"consumer_artifact_id": c1id, "source_ref_canonical": "N2", "resolution": "uploaded_cv", "producer_artifact_id": p1id},
    ]}
    requests.put(BASE + "/api/nested/sessions/" + sid + "/links", json=links_payload)
    r = requests.post(BASE + "/api/nested/sessions/" + sid + "/validate")
    ok_, data = j(r)
    if not ok_:
        return ok(False, "cycle validate exception: " + data)
    r1 = ok(data.get("valid") is False, "cycle session invalid")
    codes = [e.get("code") for e in data.get("errors", [])]
    r2 = ok("GRAPH_CYCLE" in codes, "GRAPH_CYCLE error present")
    return r1 and r2

# ----------------------------------------------------------------
# T17 - generate SQL
# ----------------------------------------------------------------
def t17():
    sid = sess()
    chunks = [{"sql_content": "SELECT id, name FROM src_tbl", "chunk_id": "c1"}]
    add_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("GEN", chunks=chunks), "file_name": "gen.xlsx"}).json()
    aid = add_resp.get("artifact", {}).get("artifact_id")
    requests.patch(BASE + "/api/nested/sessions/" + sid + "/cvs/" + aid,
        json={"emission_mode": "emit_view", "target_view_name": "GEN_OUT"})
    gen_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/generate").json()
    tid = gen_resp.get("task_id")
    if not tid:
        return ok(False, "no task_id returned")
    for i in range(20):
        time.sleep(1)
        tr = requests.get(BASE + "/api/nested/tasks/" + tid).json()
        if tr.get("status") == "COMPLETED":
            dr = requests.get(BASE + "/api/nested/tasks/" + tid + "/download")
            r1 = ok(dr.status_code == 200, "download status=" + str(dr.status_code))
            r2 = ok("CREATE" in dr.text or "VIEW" in dr.text, "SQL contains CREATE/VIEW")
            return r1 and r2
        if tr.get("status") == "FAILED":
            return ok(False, "generation failed: " + str(tr.get("message", "")[:80]))
    return ok(False, "timeout after 20s")

# ----------------------------------------------------------------
# T18 - generate PySpark
# ----------------------------------------------------------------
def t18():
    sid = sess("databricks", "pyspark")
    chunks = [{"sql_content": "SELECT a, b FROM tbl", "chunk_id": "c1"}]
    add_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("PK", chunks=chunks), "file_name": "pk.xlsx"}).json()
    aid = add_resp.get("artifact", {}).get("artifact_id")
    requests.patch(BASE + "/api/nested/sessions/" + sid + "/cvs/" + aid,
        json={"emission_mode": "emit_view", "target_view_name": "PK_OUT"})
    gen_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/generate").json()
    tid = gen_resp.get("task_id")
    if not tid:
        return ok(False, "no pyspark task_id")
    for i in range(20):
        time.sleep(1)
        tr = requests.get(BASE + "/api/nested/tasks/" + tid).json()
        if tr.get("status") == "COMPLETED":
            dr = requests.get(BASE + "/api/nested/tasks/" + tid + "/download")
            return ok(dr.status_code == 200, "pyspark download status=" + str(dr.status_code))
        if tr.get("status") == "FAILED":
            return ok(False, "pyspark failed: " + str(tr.get("message", "")[:80]))
    return ok(False, "pyspark timeout")

# ----------------------------------------------------------------
# T19 - delete session
# ----------------------------------------------------------------
def t19():
    sid = sess()
    del_resp = requests.delete(BASE + "/api/nested/sessions/" + sid)
    ok_, _ = j(del_resp)
    if not ok_:
        return ok(False, "delete request failed")
    get_resp = requests.get(BASE + "/api/nested/sessions/" + sid)
    return ok(get_resp.status_code == 404, "deleted session is 404: " + str(get_resp.status_code))

# ----------------------------------------------------------------
# T20 - update global mappings
# ----------------------------------------------------------------
def t20():
    sid = sess()
    add_resp = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs",
        json={"file_content": cv_payload("MAPCV"), "file_name": "map.xlsx"}).json()
    aid = add_resp.get("artifact", {}).get("artifact_id")
    map_payload = {"mappings": [{"source_ref_canonical": "SRC_TBL", "source_column_raw": "col_a", "target_table": "tgt_tbl", "target_column": "new_col", "artifact_id": aid}]}
    put_resp = requests.put(BASE + "/api/nested/sessions/" + sid + "/mappings", json=map_payload)
    ok_, _ = j(put_resp)
    if not ok_:
        return ok(False, "put mappings failed")
    get_resp = requests.get(BASE + "/api/nested/sessions/" + sid).json()
    maps = get_resp.get("session", {}).get("global_mappings", [])
    r1 = ok(len(maps) == 1, "1 mapping saved")
    r2 = ok(maps[0].get("target_table") == "tgt_tbl", "target_table correct")
    return r1 and r2


def nested_xlsx_bytes(cv_name="CHILD_XLSX", source_table="SRC_TBL"):
    try:
        import pandas as pd
        from cryptography.fernet import Fernet
        import base64, hashlib
    except ImportError as e:
        raise RuntimeError("XLSX tests require pandas, openpyxl, and cryptography") from e

    workbook = io.BytesIO()
    sql_info = pd.DataFrame([{
        "Node name": cv_name,
        "SourceTable_mapping_fields": str({source_table: ["ID"]}),
        "Chunk SQL Primary Optimized Base": f"SELECT ID FROM {source_table}",
    }])
    mapping_info = pd.DataFrame([{
        "Original Table": source_table,
        "Original Column": "ID",
        "New Table": source_table,
        "New Column": "ID",
    }])
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        sql_info.to_excel(writer, sheet_name="sql info", index=False)
        mapping_info.to_excel(writer, sheet_name="mapping info", index=False)

    key = base64.urlsafe_b64encode(hashlib.sha256(b"mypassword123la").digest())
    return Fernet(key).encrypt(workbook.getvalue())


def upload_xlsx(sid, data, **fields):
    return requests.post(
        BASE + "/api/nested/sessions/" + sid + "/cvs/xlsx",
        files={"xlsxFile": ("nested.xlsx", data, "application/octet-stream")},
        data=fields,
    )


# ----------------------------------------------------------------
# T21 - XLSX inspection must not mutate session
# ----------------------------------------------------------------
def t21():
    sid = sess()
    before = requests.get(BASE + "/api/nested/sessions/" + sid).json()["session"]
    r = upload_xlsx(sid, nested_xlsx_bytes(), inspectOnly="true")
    ok_, data = j(r)
    after = requests.get(BASE + "/api/nested/sessions/" + sid).json()["session"]
    if not ok_:
        return ok(False, "inspect exception: " + data)
    r1 = ok(data.get("success") is True, "inspect succeeds")
    r2 = ok(len(data.get("sql_info", [])) == 1, "inspect returns sql_info")
    r3 = ok(len(before["artifacts"]) == len(after["artifacts"]) == 0, "inspect does not add artifact")
    return r1 and r2 and r3


# ----------------------------------------------------------------
# T22 - parent-aware XLSX creates one explicit link
# ----------------------------------------------------------------
def t22():
    sid = sess()
    deps = [{"source_ref_raw": "CHILD_REF", "source_ref_canonical": "CHILD_REF", "object_kind": "calculation_view", "referenced_by_node": "root", "required_columns": ["ID"]}]
    parent = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json={"file_content": cv_payload("PARENT_XLSX", deps=deps), "file_name": "parent.xlsx"}).json()["artifact"]
    r = upload_xlsx(sid, nested_xlsx_bytes(), parentSourceRef="CHILD_REF", parentArtifactId=parent["artifact_id"])
    ok_, data = j(r)
    if not ok_:
        return ok(False, "parent upload exception: " + data)
    links = data.get("session", {}).get("dependency_links", [])
    matches = [link for link in links if link.get("consumer_artifact_id") == parent["artifact_id"] and link.get("source_ref_canonical") == "CHILD_REF"]
    r1 = ok(r.status_code == 200 and data.get("success") is True, "parent-aware upload succeeds")
    r2 = ok(len(data.get("session", {}).get("artifacts", {})) == 2, "exactly two artifacts in session")
    r3 = ok(len(matches) == 1 and matches[0].get("producer_artifact_id") == data.get("artifact", {}).get("artifact_id"), "explicit dependency link created")
    return r1 and r2 and r3


# ----------------------------------------------------------------
# T23 - partial and invalid parent context rejected without mutation
# ----------------------------------------------------------------
def t23():
    sid = sess()
    r1 = upload_xlsx(sid, nested_xlsx_bytes(), parentSourceRef="CHILD_REF")
    r2 = upload_xlsx(sid, nested_xlsx_bytes(), parentSourceRef="CHILD_REF", parentArtifactId="missing")
    artifacts = requests.get(BASE + "/api/nested/sessions/" + sid).json()["session"]["artifacts"]
    a = ok(r1.status_code == 400, "partial parent context rejected")
    b = ok(r2.status_code == 404, "missing parent artifact rejected")
    c = ok(len(artifacts) == 0, "invalid requests do not mutate session")
    return a and b and c


# ----------------------------------------------------------------
# T24 - replacing a dependency upserts one link
# ----------------------------------------------------------------
def t24():
    sid = sess()
    deps = [{"source_ref_raw": "CHILD_REF", "source_ref_canonical": "CHILD_REF", "object_kind": "calculation_view", "referenced_by_node": "root", "required_columns": ["ID"]}]
    parent = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json={"file_content": cv_payload("PARENT_REPLACE", deps=deps), "file_name": "parent.xlsx"}).json()["artifact"]
    upload_xlsx(sid, nested_xlsx_bytes("CHILD_ONE"), parentSourceRef="CHILD_REF", parentArtifactId=parent["artifact_id"])
    second = upload_xlsx(sid, nested_xlsx_bytes("CHILD_TWO"), parentSourceRef="CHILD_REF", parentArtifactId=parent["artifact_id"]).json()
    links = second.get("session", {}).get("dependency_links", [])
    matches = [link for link in links if link.get("consumer_artifact_id") == parent["artifact_id"] and link.get("source_ref_canonical") == "CHILD_REF"]
    r1 = ok(len(matches) == 1, "replacement keeps one dependency link")
    r2 = ok(matches and matches[0].get("producer_artifact_id") == second.get("artifact", {}).get("artifact_id"), "replacement points to newest artifact")
    return r1 and r2


# ----------------------------------------------------------------
# T25 - deleting nested producer prunes explicit link
# ----------------------------------------------------------------
def t25():
    sid = sess()
    deps = [{"source_ref_raw": "CHILD_REF", "source_ref_canonical": "CHILD_REF", "object_kind": "calculation_view", "referenced_by_node": "root", "required_columns": ["ID"]}]
    parent = requests.post(BASE + "/api/nested/sessions/" + sid + "/cvs", json={"file_content": cv_payload("PARENT_DELETE", deps=deps), "file_name": "parent.xlsx"}).json()["artifact"]
    added = upload_xlsx(sid, nested_xlsx_bytes(), parentSourceRef="CHILD_REF", parentArtifactId=parent["artifact_id"]).json()
    child_id = added["artifact"]["artifact_id"]
    requests.delete(BASE + "/api/nested/sessions/" + sid + "/cvs/" + child_id)
    session = requests.get(BASE + "/api/nested/sessions/" + sid).json()["session"]
    r1 = ok(child_id not in session["artifacts"], "nested producer removed")
    r2 = ok(all(link.get("producer_artifact_id") != child_id for link in session["dependency_links"]), "nested link pruned")
    return r1 and r2


# ----------------------------------------------------------------
# T26 - PySpark rejected on SQL-only platforms
# ----------------------------------------------------------------
def t26():
    r = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": "bigquery", "output_format": "pyspark"})
    a = ok(r.status_code == 400, "bigquery + pyspark rejected: " + str(r.status_code))
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    b = ok("PySpark" in (body.get("error") or ""), "error mentions PySpark gating")
    r2 = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": "azure", "output_format": "pyspark"})
    c = ok(r2.status_code == 200, "azure + pyspark accepted: " + str(r2.status_code))
    requests.delete(BASE + "/api/nested/sessions/" + (r2.json().get("session", {}).get("session_id") or ""))
    r3 = requests.post(BASE + "/api/nested/sessions", json={"target_dialect": "databricks", "output_format": "pyspark"})
    d = ok(r3.status_code == 200, "databricks + pyspark accepted: " + str(r3.status_code))
    requests.delete(BASE + "/api/nested/sessions/" + (r3.json().get("session", {}).get("session_id") or ""))
    return a and b and c and d


# ----------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------
TESTS = [
    ("T01", t01),
    ("T02", t02),
    ("T03", t03),
    ("T04", t04),
    ("T05", t05),
    ("T06", t06),
    ("T07", t07),
    ("T08", t08),
    ("T09", t09),
    ("T10", t10),
    ("T11", t11),
    ("T12", t12),
    ("T13", t13),
    ("T14", t14),
    ("T15", t15),
    ("T16", t16),
    ("T17", t17),
    ("T18", t18),
    ("T19", t19),
    ("T20", t20),
    ("T21", t21),
    ("T22", t22),
    ("T23", t23),
    ("T24", t24),
    ("T25", t25),
    ("T26", t26),
]

if __name__ == "__main__":
    print("=" * 60)
    print(" Nested CV Flattener - 25 API Tests")
    print(" Target: " + BASE)
    print("=" * 60)
    passed = 0
    failed = 0
    for num, fn in TESTS:
        print()
        print("[" + num + "] " + fn.__name__ + "...")
        try:
            result = fn()
        except Exception as e:
            result = ok(False, "EXCEPTION: " + str(e)[:80])
        if result:
            passed += 1
        else:
            failed += 1
    cleanup_all()
    print()
    print("=" * 60)
    print(" RESULTS: " + str(passed) + " passed, " + str(failed) + " failed, " + str(passed + failed) + " total")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
