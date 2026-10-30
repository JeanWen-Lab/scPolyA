"""Independent arithmetic controls; never call native quantification for expectations."""
import copy
import json

import pytest

from scpolya_agent.gene_diagnostics import diagnose_gene_units, DiagnosticValidationError


NAMES = ["distal_fraction_all_assigned", "distal_fraction_retained_all_contexts",
         "distal_fraction_terminal_conditional", "intronic_fraction_intronic_plus_terminal"]


def policy(**changes):
    return {"quantification_mode": "terminal-aware", "sample_ids": ["s"],
            "complete_locus_counts": True, "complete_locus_catalogue": True,
            "sparse_omission_is_zero": True, "sample_binding": "explicit_sample_id",
            "general_mispriming_threshold": 0.5, "intronic_mispriming_threshold": 0.5,
            "psi_min_gene_umis": 1, **changes}


def site(peak, feature, *, sample="s", gene="001", strand="+", ratio=0.1,
         distance=10, distal=None, n=None, order=None):
    passed = ratio is not None and ratio <= 0.5
    return {"sample_id": sample, "peak_name": peak, "gene_id": gene, "strand": strand,
            "feature": feature, "misprimed_ratio": ratio, "general_mispriming_pass": passed,
            "intronic_mispriming_pass": passed, "mispriming_pass": passed,
            "distance_to_stop_codon": distance, "is_distal_peak": distal,
            "n_peaks_in_gene": n, "peak_order_in_gene": order}


def catalogue():
    return [site("P", "Terminal_exon", distal=False, n=2, order=1),
            site("D", "Downstream_of_terminal_exon", distal=True, distance=100, n=2, order=2),
            site("I", "Intron")]


def counts(p=40, d=40, i=20):
    return [{"sample_id": "s", "cell_barcode": "NA-1", "peak_name": peak, "umi_count": c}
            for peak, c in zip(["P", "D", "I"], [p, d, i])]


def run(cs=None, cat=None, registry=None, pol=None, provenance=None):
    return diagnose_gene_units(counts() if cs is None else cs,
                               catalogue() if cat is None else cat, registry,
                               policy() if pol is None else pol,
                               {"run_commit": None} if provenance is None else provenance)


def metrics(result):
    return {r["metric_name"]: r for r in result["metric_rows"]}


def vals(result):
    m = metrics(result)
    return [m[n]["value_or_null"] for n in NAMES]


def unit():
    return {"sample_id": "s", "cell_barcode": "NA-1", "gene_id": "001", "strand": "+"}


def test_document_allocation_control():
    a, b = run(), run(counts(20, 20, 60))
    assert vals(a) == [0.4, 0.4, 0.5, 0.2]
    assert vals(b) == [0.2, 0.2, 0.5, 0.6]
    assert [a["denominator_rows"][0][k] for k in ("S_assigned_raw", "S_retained", "T", "I", "D")] == [100,100,80,20,40]
    assert metrics(b)[NAMES[3]]["denominator"] == 100


def test_other_context_and_failed_intronic_audit_are_distinct():
    cat = catalogue() + [site("O", "Internal_exon"), site("F", "Intron", ratio=0.8)]
    cs = counts() + [dict(counts()[0], peak_name="O", umi_count=25),
                     dict(counts()[0], peak_name="F", umi_count=30)]
    r = run(cs, cat)
    assert vals(r) == [40/155, 0.32, 0.5, 0.2]
    t = r["denominator_rows"][0]
    assert [t[k] for k in ("S_assigned_raw", "S_retained", "T", "I", "O_retained", "failed_intronic_audit_umis")] == [155,125,80,20,25,30]
    assert t["S_retained"] != t["I"] + t["T"]


def test_sparse_zero_explicit_zero_registry_and_missing_are_different():
    r = run(counts(10,0,0)[:1])
    assert vals(r) == [0,0,0,0]
    assert vals(run([], registry=[unit()])) == [None]*4
    assert run([])["metric_rows"] == []
    with pytest.raises(DiagnosticValidationError, match="incomplete_scope"):
        run(pol=policy(complete_locus_counts=False))
    with pytest.raises(DiagnosticValidationError, match="invalid_records"):
        diagnose_gene_units(None, catalogue(), None, policy(), {})


def test_zero_terminal_with_known_distal():
    r = run(counts(0,0,10))
    assert vals(r) == [0,0,None,1]
    assert metrics(r)[NAMES[2]]["denominator"] == 0


def test_single_terminal_control():
    cat = [site("D", "Terminal_exon", distal=True, n=1, order=1), site("I", "Intron")]
    r = run(counts(0,30,10)[1:], cat)
    assert vals(r) == [0.75,0.75,1,0.25]
    assert metrics(r)[NAMES[2]]["status"] == "single_terminal_control"
    assert metrics(r)[NAMES[2]]["recipe_native_comparable_distal_value_or_null"] is None
    assert not r["denominator_rows"][0]["has_multiple_eligible_terminal_sites"]


def test_minus_strand_uses_native_distance_not_coordinate():
    cat = catalogue()
    for row in cat:
        row["strand"] = "-"
    cat[0].update(start=200, end=201)
    cat[1].update(start=100, end=101)
    r = run(counts(30,70,0), cat)
    assert vals(r) == [0.7,0.7,0.7,0]
    assert r["denominator_rows"][0]["unique_distal_peak_name"] == "D"


@pytest.mark.parametrize("mode", ["tie", "multiple_flags", "nan", "inf", "missing", "wrong_flag"])
def test_distal_ambiguity_preserves_intronic_fraction(mode):
    cat = catalogue()
    if mode == "tie": cat[0]["distance_to_stop_codon"] = 100
    if mode == "multiple_flags": cat[0]["is_distal_peak"] = True
    if mode in ("nan", "inf", "missing"):
        cat[1]["distance_to_stop_codon"] = {"nan":float("nan"),"inf":float("inf"),"missing":None}[mode]
    if mode == "wrong_flag": cat[0]["is_distal_peak"],cat[1]["is_distal_peak"] = True,False
    r = run(cat=cat)
    assert vals(r) == [None,None,None,0.2]
    json.dumps(r, allow_nan=False)


def test_failed_and_no_evidence_vs_no_intronic_candidate():
    cat = catalogue()
    cat[2] = site("I", "Intron", ratio=0.8)
    assert vals(run(cat=cat)) == [0.4,0.5,0.5,None]
    cat[2] = site("I", "Intron", ratio=None)
    r = run(counts()[:2],cat)
    assert metrics(r)[NAMES[3]]["reason_codes"] == ["no_retained_intronic_evidence"]
    with pytest.raises(DiagnosticValidationError, match="inconsistent_native_count_population"):
        run(cat=cat)
    absent = run(counts()[:2],cat[:2])
    assert vals(absent) == [0.5,0.5,0.5,0]
    assert "no_intronic_candidate_not_absence_of_biology" in metrics(absent)[NAMES[3]]["notes"]
    only_i = run(counts()[2:],cat=catalogue()[2:])
    assert vals(only_i) == [None,None,None,None]


def test_identity_namespaces_and_valid_text_NA():
    cats, cs = [], []
    for sample, gene, strand, d in [("s","001","+",40),("s","1","+",10),("s","NA","-",20),("s2","001","+",30)]:
        for row in catalogue():
            cats.append(dict(row, sample_id=sample, gene_id=gene, strand=strand,
                             peak_name=f"{gene}{strand}{row['peak_name']}", gene_name="same"))
        for row in counts(40,d,20):
            cs.append(dict(row,sample_id=sample,peak_name=f"{gene}{strand}{row['peak_name']}"))
    r = run(cs,cats,pol=policy(sample_ids=["s2","s"]))
    assert len(r["denominator_rows"]) == 4
    assert [x["D"] for x in r["denominator_rows"]] == [40,10,20,30]


def test_native_mask_and_caller_provenance_not_certified():
    r = run(pol=policy(psi_min_gene_umis=100), provenance={"run_commit":"claimed", "counts_sha256":"claimed"})
    assert vals(r) == [0.4,0.4,None,0.2]
    m = metrics(r)[NAMES[2]]
    assert m["raw_arithmetic_value_or_null"] == 0.5
    assert m["recipe_native_comparable_distal_value_or_null"] is None
    assert "below_native_psi_threshold" in m["reason_codes"]
    assert r["provenance"]["trust"] == "caller_supplied_not_certified"
    assert "file_hashes" in r["provenance"]["not_verified"]


def test_unassigned_is_audited_unknown_is_error():
    cat = catalogue() + [site("U", "Intergenic", gene=None, strand=".")]
    cs = counts() + [dict(counts()[0], peak_name="U", umi_count=50)]
    r = run(cs,cat)
    assert vals(r) == [0.4,0.4,0.5,0.2]
    assert r["unassigned_count_audit"] == [{"sample_id":"s", "count_rows":1,"umi_count":50}]
    with pytest.raises(DiagnosticValidationError, match="unknown_peak_mapping"):
        run(cs,catalogue())


@pytest.mark.parametrize("bad", [-1,1.5,"2",True,float("inf"),2**53])
def test_invalid_counts(bad):
    cs = counts(); cs[0]["umi_count"] = bad
    with pytest.raises(DiagnosticValidationError, match="invalid_integer"):
        run(cs)


@pytest.mark.parametrize("field,value", [("mispriming_pass","False"),("is_distal_peak","False"),
    ("general_mispriming_pass",False),("misprimed_ratio",float("nan")),("distance_to_stop_codon","10"),
    ("gene_id",1),("n_peaks_in_gene",3)])
def test_invalid_catalogue(field,value):
    cat = catalogue(); cat[0][field] = value
    with pytest.raises(DiagnosticValidationError): run(cat=cat)


def test_duplicates_and_sample_conflicts():
    with pytest.raises(DiagnosticValidationError, match="duplicate_count_row"): run(counts()+counts()[:1])
    with pytest.raises(DiagnosticValidationError, match="duplicate_catalogue_peak"): run(cat=catalogue()+catalogue()[:1])
    with pytest.raises(DiagnosticValidationError, match="duplicate_registry_unit"): run(registry=[unit(),unit()])
    cs = counts(); cs[0]["sample_id"] = "elsewhere"
    with pytest.raises(DiagnosticValidationError, match="sample_binding"): run(cs)


@pytest.mark.parametrize("name,value", [("count_rows",2),("catalogue_rows",2),("registry_rows",1),("output_units",1),("output_records",1)])
def test_hard_limits_no_truncation(name,value):
    registry = [unit(),dict(unit(),cell_barcode="another")]
    with pytest.raises(DiagnosticValidationError, match="limit_exceeded"):
        run(registry=registry,pol=policy(limits={name:value}))


def test_inputs_unchanged_and_output_stably_serializable():
    cs, cat, reg, pol, prov = counts(),catalogue(),[unit()],policy(),{"run_commit":None}
    before = copy.deepcopy((cs,cat,reg,pol,prov))
    a = diagnose_gene_units(cs,cat,reg,pol,prov)
    b = diagnose_gene_units(list(reversed(cs)),list(reversed(cat)),reg,pol,prov)
    assert (cs,cat,reg,pol,prov) == before
    assert json.dumps(a,sort_keys=True,allow_nan=False) == json.dumps(b,sort_keys=True,allow_nan=False)
    a["provenance"]["declared"]["run_commit"] = "changed"
    assert prov["run_commit"] is None


def test_exception_is_structured():
    with pytest.raises(DiagnosticValidationError) as caught:
        run(pol=policy(sparse_omission_is_zero=False))
    assert caught.value.as_dict()["code"] == "incomplete_scope"


def canonical_bytes(result):
    return json.dumps(result, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@pytest.mark.parametrize("case,expected", [
    ("zero", (True,True,False,False,False)),
    ("zero_mask0", (True,True,False,True,False)),
    ("low", (True,True,True,False,False)),
    ("single", (False,True,True,True,False)),
    ("tie", (True,False,True,True,False)),
    ("adequate", (True,True,True,True,True))])
def test_structure_depth_and_recipe_value_are_separate(case, expected):
    cat, cs, reg, pol = catalogue(), counts(), None, policy()
    if case.startswith("zero"):
        cs,reg = [],[unit()]
        if case == "zero_mask0": pol["psi_min_gene_umis"] = 0
    if case == "low": cs,pol = counts(2,2,0),policy(psi_min_gene_umis=100)
    if case == "single":
        cat = [site("D","Terminal_exon",distal=True,n=1,order=1),site("I","Intron")]
        cs = counts(0,30,10)[1:]
    if case == "tie": cat[0]["distance_to_stop_codon"] = 100
    r = run(cs,cat,reg,pol)
    d = r["denominator_rows"][0]
    fields = ["has_multiple_eligible_terminal_sites", "has_unique_distal_metadata",
              "positive_terminal_denominator", "terminal_depth_passes_native_mask",
              "recipe_native_comparable_distal_value_available"]
    assert tuple(d[k] for k in fields) == expected
    m = metrics(r)[NAMES[2]]
    assert (m["recipe_native_comparable_distal_value_or_null"] is not None) == expected[-1]
    assert "switching_eligible" not in d
    assert "native_psi_eligible" not in m
    assert "native_comparable_masked_value_or_null" not in m


def test_review_twenty_thousand_units_rejected_before_result():
    registry = [dict(unit(),cell_barcode=f"{i:05d}" + "c"*400) for i in range(20_000)]
    with pytest.raises(DiagnosticValidationError) as caught:
        run([],registry=registry)
    assert caught.value.code == "limit_exceeded"
    assert caught.value.detail == "registry_rows"


def test_within_row_caps_long_ids_hit_output_byte_budget(monkeypatch):
    # This case is inside the new unit/record caps; the byte budget must bind.
    from scpolya_agent import gene_diagnostics as module
    cat = catalogue()
    for row in cat:
        row.update(sample_id="s"*400,gene_id="g"*400)
    registry = [dict(unit(),sample_id="s"*400,gene_id="g"*400,
                     cell_barcode=f"{i:03d}"+"c"*400) for i in range(256)]
    original = module.json.JSONEncoder

    class NoWholePopulatedResult(original):
        def iterencode(self, obj, *args, **kwargs):
            if isinstance(obj,dict) and obj.get("schema") == module.SCHEMA:
                assert not obj["denominator_rows"]  # only the empty-list envelope
                assert not obj["metric_rows"]
            return super().iterencode(obj,*args,**kwargs)

    monkeypatch.setattr(module.json,"JSONEncoder",NoWholePopulatedResult)
    with pytest.raises(DiagnosticValidationError) as caught:
        run([],cat,registry,policy(sample_ids=["s"*400]))
    assert caught.value.code == "limit_exceeded"
    assert caught.value.detail == "output_json_bytes"


def test_aggregate_input_text_budget_includes_repeated_text():
    cs = [dict(counts()[0],cell_barcode=f"{i:05d}"+"c"*500,
               peak_name="P"*500) for i in range(5000)]
    with pytest.raises(DiagnosticValidationError) as caught:
        run(cs)
    assert caught.value.code == "limit_exceeded"
    assert caught.value.detail == "input_text_bytes"


@pytest.mark.parametrize("location", ["extra_value","extra_key","policy_extra"])
def test_input_budget_also_counts_unused_text(location):
    cs,pol = counts(),policy()
    oversized = "x" * (4*1024*1024+1)
    if location == "extra_value": cs[0]["unused"] = oversized
    if location == "extra_key": cs[0][oversized] = None
    if location == "policy_extra": pol["unused"] = oversized
    with pytest.raises(DiagnosticValidationError) as caught: run(cs,pol=pol)
    assert caught.value.detail == "input_text_bytes"


def test_canonical_output_exact_boundary_all_sections_and_unicode():
    # Quotes, backslashes and Unicode prove escaped-byte rather than len(text) accounting.
    prov = {"note": '\u4e2d\U0001f600"\\' * 40}
    initial = run(provenance=prov)
    initial_size = len(canonical_bytes(initial))
    # The budget's own decimal value is part of the result envelope.
    bound = initial_size - len(str(1024*1024)) + len(str(initial_size))
    r = run(provenance=prov, pol=policy(limits={"output_json_bytes":bound}))
    assert len(canonical_bytes(r)) == bound
    with pytest.raises(DiagnosticValidationError) as caught:
        run(provenance=prov, pol=policy(limits={"output_json_bytes":bound-1}))
    assert caught.value.detail == "output_json_bytes"


def test_envelope_and_audit_only_outputs_consume_byte_budget():
    with pytest.raises(DiagnosticValidationError) as caught:
        run([],cat=[],provenance={"note":"p"*2000},pol=policy(limits={"output_json_bytes":1500}))
    assert caught.value.detail == "output_json_bytes"
    cat = [site(f"U{i}","Intergenic",gene=None,strand=".") for i in range(30)]
    with pytest.raises(DiagnosticValidationError) as caught:
        run([],cat=cat,pol=policy(limits={"output_json_bytes":2000}))
    assert caught.value.detail == "output_json_bytes"


@pytest.mark.parametrize("name", ["output_units","output_records","output_json_bytes","input_text_bytes"])
def test_new_limits_cannot_be_raised(name):
    from scpolya_agent.gene_diagnostics import LIMITS
    with pytest.raises(DiagnosticValidationError,match="invalid_limits"):
        run(pol=policy(limits={name:LIMITS[name]+1}))


def test_cyclic_unused_input_is_bounded_and_structured():
    extra = []; extra.append(extra)
    with pytest.raises(DiagnosticValidationError) as caught: run(pol=policy(unused=extra))
    assert caught.value.code == "limit_exceeded"
    assert caught.value.detail == "input_depth (maximum 8)"
