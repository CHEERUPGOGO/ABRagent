"""协议扩展权限：先建议、后签发，等值转换和文献采用另作只读复核。"""
import copy
import json
import math
import re
from decimal import Decimal, InvalidOperation

from .scheme_ledger import digest

VERSION = 1
AUTHORITY_VERSION = 2


def _number(value):
    """仅转换完整有限十进制表示；编号、范围、比较符和单位混写不猜。"""
    if type(value) in (int, float):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if not isinstance(value, str) or not re.fullmatch(
            r"[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", value.strip()):
        return None
    try:
        number = Decimal(value.strip())
        if not number.is_finite() or not math.isfinite(float(number)):
            return None
        converted = int(number) if number == number.to_integral_value() else float(number)
        return converted if Decimal(str(converted)) == number else None
    except (InvalidOperation, OverflowError, ValueError):
        return None


def _scope_destination(data, current, path, row):
    """只隔离误占规范字段的新工况声明；保留规范身份、全部条件及原始取值。"""
    from .numeric_consistency import canonical, context, identity, UNITS
    from .numeric_bindings import declarations, normalize_declaration
    from .scheme_ledger import validate_field
    from .writer_protocol import normalize
    if (path not in UNITS or canonical(path, row) != path
            or context(row) == ('main', 'main', '[]')
            or row.get('basis') not in ('engineering_proposal', 'user_requirement')
            or row.get('locked') or path in current
            or any(canonical(key, value) == path for key, value in current.items())):
        return None
    domain, tail = path.split('.', 1)
    new = domain + '.scoped_' + digest([path, context(row)])[:12] + '.' + tail
    if new in data.get('parameters', {}) or new in current or canonical(new) != new:
        return None
    after = dict(copy.deepcopy(row), canonical_path=path)
    try:
        # 迁移不能绕过规范字段原有的类型、单位、范围或权限检查。
        normalized = normalize({'parameters': {path: row}, 'sections': {}})['parameters'][path]
        validate_field(path, normalized)
        declarations(normalize({'parameters': {new: after}, 'sections': {}}), current)
        if identity(path, normalize_declaration(row)) != identity(new, normalize_declaration(after)):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return new, after


def candidates(data, current, errors, evidence=(), *, allow_scope=True):
    """程序只为本轮报错的新字段提出有限转换，不给模型任意改值/改来源权限。"""
    from .numeric_consistency import canonical, unit, UNITS
    from .scheme_ledger import canonical_unit
    from .writer_references import body_entries
    paths = {row['path'][1] for row in errors if row.get('code') == 'PARAMETER'
             and isinstance(row.get('path'), list) and len(row['path']) == 2}
    parameters = data.get('parameters') or {}
    evidence_ids = {e.get('passage_id') for e in evidence if isinstance(e, dict)}
    reserved = {canonical(path, row) for path, row in current.items()}
    result = {}
    for path in sorted(paths):
        row = parameters.get(path)
        if (not isinstance(row, dict) or path in current or row.get('locked')
                or canonical(path, row) in reserved
                or not isinstance(row.get('source'), str) or not row['source'].strip()):
            continue
        refs = [body_path for body_path, text in body_entries(data) if '{{param:' + path + '}}' in text]
        if not refs:
            continue
        relocation = _scope_destination(data, current, path, row) if allow_scope else None
        if relocation:
            new, after = relocation
            proof = {'version': AUTHORITY_VERSION, 'base_hash': digest(data), 'parameters_hash': digest(current),
                     'path': path, 'new_path': new, 'before': copy.deepcopy(row), 'after': after,
                     'actions': ['scope_relocation'], 'body_paths': refs}
            result['correction-' + digest(proof)[:20]] = proof
            continue
        if row.get('basis') not in ('literature', 'engineering_proposal'):
            continue
        after, actions = copy.deepcopy(row), []
        if row['basis'] == 'literature':
            cited = set(re.findall(r'\[([A-Za-z0-9_-]+)\]', row['source']))
            if not cited & evidence_ids:
                continue
            # 工程采用是待核验候选；须先有正文原句，再经独立复核，不继承文献的实测身份。
            after['basis'] = 'engineering_proposal'
            actions.append('adopt_as_proposal')
        numeric = _number(row.get('value'))
        if isinstance(row.get('value'), str) and numeric is not None:
            # 显式text声明可能是编号；只能处理原始未指定类型或明确物理类型的数值字符串。
            if row.get('quantity_kind') not in (None, 'physical', 'ratio', 'fraction', 'site_fraction',
                                               'mass_fraction', 'volume_fraction', 'mole_fraction'):
                continue
            after['value'] = numeric
            after.setdefault('quantity_kind', 'physical')
            actions.append('numeric_string')
        raw_unit = unit(row.get('unit'))
        if raw_unit in ('%', 'mol%', 'at%', 'wt%', 'vol%'):
            denominator = row.get('denominator')
            expected = canonical_unit(path)
            if expected is None:
                expected = UNITS.get(canonical(path, row))
            if (numeric is None or not 0 <= numeric <= 100 or not isinstance(denominator, str)
                    or not denominator.strip() or row.get('quantity_kind') not in (
                        'ratio', 'fraction', 'site_fraction', 'mass_fraction', 'volume_fraction', 'mole_fraction')
                    or expected not in (None, '')):
                continue
            fraction = Decimal(str(numeric)) / Decimal(100)
            converted = float(fraction)
            if Decimal(str(converted)) != fraction:
                continue
            after.update(value=converted, unit='')
            if after.get('quantity_kind') == 'fraction':
                after['quantity_kind'] = 'ratio'
            actions.append('percent_fraction')
        if not actions:
            continue
        # 任何其他非法字段/语义仍留给原契约拒绝，不能借新权限删除它们。
        proof = {'version': VERSION, 'base_hash': digest(data), 'parameters_hash': digest(current),
                 'path': path, 'before': copy.deepcopy(row), 'after': after, 'actions': actions,
                 'body_paths': refs}
        identifier = 'correction-' + digest(proof)[:20]
        result[identifier] = proof
    return result


def _call(client, state, key, request, system, tag, save):
    """调用前占用机会；断线/失败原样保存，恢复仅复用已返回响应。"""
    signature = digest(request)
    saved = state.get(key)
    if saved is not None and saved.get('input_hash') != signature:
        raise ValueError('协议建议/复核输入已变化，拒绝恢复旧调用')
    if saved is None:
        if not getattr(client, 'available', False):
            raise ValueError('协议建议/复核模型不可用，不能授予扩展权限')
        saved = state[key] = {'input_hash': signature, 'attempted': True, 'request': copy.deepcopy(request)}
        save()
        had_single = hasattr(client, 'single_attempt')
        old_single = getattr(client, 'single_attempt', False)
        try:
            client.single_attempt = True
            saved['raw_response'] = client.chat(system, json.dumps(dict(request, input_hash=signature),
                ensure_ascii=False), temperature=0, json_mode=True, trace_tag=tag)
            saved['response_meta'] = copy.deepcopy(getattr(client, 'last_response', {}))
            save()
        except Exception as exc:
            saved['error'] = str(exc)
            save()
            raise
        finally:
            if had_single:
                client.single_attempt = old_single
            else:
                delattr(client, 'single_attempt')
    if 'raw_response' not in saved:
        raise ValueError('协议建议/复核已占用但未返回，不能自动重发')
    from .writer_protocol import decode
    response = decode(saved['raw_response'])
    if not isinstance(response, dict) or response.get('input_hash') != signature:
        raise ValueError('协议建议/复核基准不匹配')
    return saved, response


def _located_refs(data, path, refs):
    from .writer_references import body_entries
    entries = {json.dumps(p): t for p, t in body_entries(data)}
    if not isinstance(refs, list) or not refs:
        raise ValueError('协议修改建议缺少当前正文定位')
    result = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'body_path', 'quote'}:
            raise ValueError('协议建议只能引用正文路径及原句')
        quote = ref.get('quote')
        body = entries.get(json.dumps(ref.get('body_path')))
        if (not isinstance(quote, str) or not quote.strip() or body is None or body.count(quote) != 1
                or '{{param:' + path + '}}' not in quote):
            raise ValueError('协议修改建议原句不唯一或未引用该字段')
        result.append(copy.deepcopy(ref))
    return result


def advise(client, data, current, errors, evidence, context, state, save, *, raw_data=None, allow_scope=True):
    """先调用模型给出具体建议，程序再把已核验的建议签发为不可任意改写的转换。"""
    # 使用规范前声明判别显式text与程序补出的text，避免数值字符串丢失原始类型。
    original = copy.deepcopy(data)
    if raw_data is not None:
        from .writer_protocol import normalize
        for path, row in (raw_data.get('parameters') or {}).items():
            raw_view = normalize({'parameters': {path: row}, 'sections': {}})['parameters'][path]
            if raw_view == data.get('parameters', {}).get(path):
                original['parameters'][path] = copy.deepcopy(row)
    choices = candidates(original, current, errors, evidence, allow_scope=allow_scope)
    if not choices:
        return {}
    version = AUTHORITY_VERSION if any('scope_relocation' in c['actions'] for c in choices.values()) else VERSION
    request = {'version': version, 'document': data, 'original_document': original,
               'current_parameters': current, 'errors': errors, 'candidate_corrections': choices,
               'evidence': evidence, 'read_only_context': context}
    saved, response = _call(client, state, 'parameter_advice', request,
        '你是参数协议修复的只读诊断员，先给具体修改建议，不能直接改稿或授权。'
        '只评估candidate_corrections：每个id都返回apply或defer、具体reason和正文refs。'
        '输出JSON {"input_hash":"给定哈希","recommendations":[{"id":"候选id",'
        '"decision":"apply|defer","reason":"逐字段说明修改前后及依据",'
        '"refs":[{"body_path":["sections","goals","body"],"quote":"含该参数引用的唯一原句"}]}]}。'
        '文献参考值只有当前正文明确采用为待验证方案参数时，才建议adopt_as_proposal；'
        '文献原样品/工况/性能、候选对照值不得晋升主方案，来源原文仍保留。'
        'numeric_string必须确为数量而非编号、版本或标签；percent_fraction必须保持同一分母、'
        '同一样品/用途及工况，不把锂过量基准、位点比例、容量损失混同。'
        'scope_relocation只把带专属样品/用途/工况的规范字段搬到程序给定独立路径，'
        'canonical_path仍指原规范字段；保留value、unit、basis、source、conditions、scope、group_id和purpose。'
        '核对原正文引用是否确属该声明，迁移不得把专属工况晋升为主方案，也不能删除条件以通过检查。'
        '需要改来源、样品、条件、数值事实或补造依据的一律defer；建议不授予科研审核通过。',
        'writer_protocol_advice', save)
    rows = response.get('recommendations')
    if (not isinstance(rows, list) or len(rows) != len(choices)
            or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows)
            or {r['id'] for r in rows} != set(choices)):
        raise ValueError('协议建议缺项、重复或包含未知字段权限')
    grants = {}
    for row in rows:
        if (set(row) - {'id', 'decision', 'reason', 'refs'} or row.get('decision') not in ('apply', 'defer')
                or not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            raise ValueError('协议建议缺少有效决定或具体原因')
        if row['decision'] == 'apply':
            proposal = choices[row['id']]
            refs = _located_refs(data, proposal['path'], row.get('refs'))
            # 签发值由程序候选固定，模型不能在建议中提供另一套after值。
            grants[row['id']] = dict(copy.deepcopy(proposal), refs=refs, reason=row['reason'],
                                     document_hash=digest(data))
    saved['grants'] = grants
    saved['status'] = 'READY' if grants else 'DEFERRED'
    save()
    return grants


def apply_corrections(data, identifiers, current, grants):
    """只接受程序签发的转换编号；不接受任意value、basis或来源写权限。"""
    if not isinstance(identifiers, list) or any(not isinstance(i, str) for i in identifiers):
        raise ValueError('corrections只能包含程序签发的编号')
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('协议转换编号重复')
    updated, records, touched, mapping = copy.deepcopy(data), [], set(), {}
    for identifier in identifiers:
        grant = grants.get(identifier)
        if (not isinstance(grant, dict) or grant.get('document_hash') != digest(data)
                or grant.get('parameters_hash') != digest(current)):
            raise ValueError('协议转换未授权或基准已变化')
        path = grant['path']
        if path in touched or path in current:
            raise ValueError('协议转换字段重复或属于已有方案')
        touched.add(path)
        # 原始字段可能已完成同尺度单位/类型兼容，重算规范视图后必须逐项相同。
        from .writer_protocol import normalize
        expected = normalize({'parameters': {path: grant['before']}, 'sections': {}})['parameters'][path]
        if updated.get('parameters', {}).get(path) != expected:
            raise ValueError('协议转换原声明已变化')
        transformed = normalize({'parameters': {path: grant['after']}, 'sections': {}})['parameters'][path]
        record = dict(id=identifier, path=path, before=copy.deepcopy(expected), after=copy.deepcopy(transformed),
                      actions=grant['actions'], refs=grant['refs'], reason=grant['reason'])
        if 'scope_relocation' in grant['actions']:
            relocation = _scope_destination(data, current, path, expected)
            if (grant['actions'] != ['scope_relocation'] or not relocation
                    or grant.get('new_path') != relocation[0] or transformed != relocation[1]):
                raise ValueError('作用域迁移只能使用程序给定路径，且须保留完整原声明')
            new = relocation[0]
            if new in touched or new in updated['parameters']:
                raise ValueError('作用域迁移目标路径冲突')
            touched.add(new)
            updated['parameters'].pop(path)
            updated['parameters'][new] = transformed
            mapping[path] = new
            record['new_path'] = new
        else:
            if 'new_path' in grant:
                raise ValueError('只有作用域迁移可改变参数路径')
            updated['parameters'][path] = transformed
        records.append(record)
    if mapping:
        from .numeric_bindings import TOKEN
        from .writer_protocol import section_nodes
        def token(match):
            kind, path = match[1], match[2]
            if kind == 'param':
                path = mapping.get(path, path)
            elif path.startswith('calculated.'):
                path = 'calculated.' + mapping.get(path[11:], path[11:])
            return '{{' + kind + ':' + path + '}}'
        for node in section_nodes(updated):
            if isinstance(node.get('body'), str):
                node['body'] = TOKEN.sub(token, node['body'])
        for row in updated['parameters'].values():
            if isinstance(row.get('ratio_inputs'), list):
                row['ratio_inputs'] = [mapping.get(path, path) for path in row['ratio_inputs']]
        # 复核和恢复审计记录实际提交的依赖路径，不能仍记录迁移前引用。
        for record in records:
            record['after'] = copy.deepcopy(updated['parameters'][record.get('new_path', record['path'])])
    return updated, records


def verify(client, before, after, records, current, evidence, context, state, save):
    """独立只读复核实际采用与等值含义，不能替代后续完整科学审核。"""
    if not records:
        return
    version = AUTHORITY_VERSION if any('scope_relocation' in r['actions'] for r in records) else VERSION
    request = {'version': version, 'before': before, 'after': after, 'corrections': records,
               'current_parameters': current, 'evidence': evidence, 'read_only_context': context}
    saved, response = _call(client, state, 'parameter_review', request,
        '你是独立只读的协议转换复核员，不改任何数据、不授予科研通过。'
        '按实际before/after核对全部corrections：每个id必须返回supported和具体reason。'
        '输出JSON {"input_hash":"给定哈希","verdicts":[{"id":"转换id","supported":true或false,"reason":"依据"}]}。'
        'adopt_as_proposal只在当前正文明确把文献值选作该方案/候选的待验证建议时支持，'
        '单有引用不算采用，不能把原文献样品/其他候选/原测试工况/性能挪入主方案。'
        '原source、样品、条件、分母、用途和候选角色必须保留；不继承实测身份或宣称该材料已验证。'
        'numeric_string须确为原数量而非样品编号、版本号或代码；'
        'percent_fraction须同分母等值，不能把总阳离子分母与锂投料补偿分母混同。'
        'scope_relocation须逐项保持原取值、单位、来源、依据、分母、样品、用途、工况和规范身份，'
        '只改变存储路径并同步param/derived引用与ratio_inputs依赖；不能改正文科学含义、'
        '把专属工况当主方案或通过删除conditions/scope/group_id/purpose消除冲突。'
        '有歧义、缺证据或正文与来源不一致则supported=false；不能为修好协议而放行。',
        'writer_protocol_verify', save)
    rows = response.get('verdicts')
    ids = {r['id'] for r in records}
    if (not isinstance(rows, list) or len(rows) != len(ids)
            or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows)
            or {r['id'] for r in rows} != ids):
        raise ValueError('协议转换复核缺项、重复或编号不匹配')
    rejected = [r['id'] + '：' + str(r.get('reason') or '无具体依据') for r in rows
                if r.get('supported') is not True or not isinstance(r.get('reason'), str) or not r['reason'].strip()]
    saved['status'] = 'REJECTED' if rejected else 'ACCEPTED'
    save()
    if rejected:
        raise ValueError('协议转换语义未通过：' + '；'.join(rejected))
