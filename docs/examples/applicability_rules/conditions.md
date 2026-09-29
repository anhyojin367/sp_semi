---
applicability:
  schema_version: 1
  id: EXAMPLE_APPLICABILITY
  company: 합성제조사
  product: 합성시험제품
  review_note: 고정 합성 허가의 전체 안내·원액 단계·두 시험과 세 조건의 해석을 시험 목적으로 검토했다. 실제 제품 승인용이 아니다.
  input_layout: explicit_page_block_v1
  reviewed_document_id: 10bb521b18696dbdf48149ff4aaa34c837200db984f54f605dac45f5ad33177b
  reviewed_node_ids:
    - 1ad3df0475f28e90cba514e96d072976d44aec9d31bb580d6b61bbe634170378
    - 28e5848b2fdd2f6e449186fbe2fb1bb490da0454a59e7257ce060a1840534894
    - 0ff1d7a7b910e7918820e1faed310dc38d7d0df609c53d17e200a82f9e14167e
    - 2e9033d785f814959ba4a6a392a9a0607e7df8293e8ec1075dc48559acad37a0
    - ec01e7db60187e70a7a11596ef0ff2ddc80d4d1613abda6296db97a5ccdbe28d
  reviewed_condition_ids:
    - 9303ab4ce0254ee64a5c17498a88bda2a5974710b434b8b29d3b727b62d99b30
    - 1d5abbb50a3f83f3f900f70a8b1a43ceb8788a013e20d4d66ec7902fe7d9151a
    - e132d08d4103a2c9a772c9581e691eb194dabdd3fd46ff5e061c372fbd69d5ee
  stages:
    - source_node_id: 0ff1d7a7b910e7918820e1faed310dc38d7d0df609c53d17e200a82f9e14167e
      sp_stage: 원액
  fields:
    - {name: 용기규격, unit: mL}
    - {name: 시험차수, unit: ""}
    - {name: 농도, unit: mg/mL}
  requirements:
    # 무균시험: 원액의 수행 조건 + 자기 시험차수 조건을 모두 적용
    - source_node_id: 2e9033d785f814959ba4a6a392a9a0607e7df8293e8ec1075dc48559acad37a0
      stage_node_id: 0ff1d7a7b910e7918820e1faed310dc38d7d0df609c53d17e200a82f9e14167e
      sp_stage: 원액
      combine: all
    # 함량시험: 농도 조건은 희석 절차의 조건이지 시험 면제가 아님
    - source_node_id: ec01e7db60187e70a7a11596ef0ff2ddc80d4d1613abda6296db97a5ccdbe28d
      stage_node_id: 0ff1d7a7b910e7918820e1faed310dc38d7d0df609c53d17e200a82f9e14167e
      sp_stage: 원액
      combine: all
  conditions:
    - source_span_id: 9303ab4ce0254ee64a5c17498a88bda2a5974710b434b8b29d3b727b62d99b30
      purpose: test_applicability
      field: 용기규격
      operator: eq
      value: "100"
      unit: mL
    - source_span_id: 1d5abbb50a3f83f3f900f70a8b1a43ceb8788a013e20d4d66ec7902fe7d9151a
      purpose: test_applicability
      field: 시험차수
      operator: gte
      value: "2"
      unit: ""
    - source_span_id: e132d08d4103a2c9a772c9581e691eb194dabdd3fd46ff5e061c372fbd69d5ee
      purpose: procedure
      field: 농도
      operator: gte
      value: "10"
      unit: mg/mL
---

# 조건 적용 MD 예제 (합성 문서 전용)

실행 조건은 위 YAML이다. 아래 설명을 바꾸는 것만으로 새 연산이 생기지는 않는다.
설명을 포함한 파일 전체가 응답 지문에 들어가므로 어느 부분을 바꾸어도 이전 응답은 재사용할 수 없다.

- 100 mL 용기일 때만 원액의 두 시험을 수행한다.
- 무균시험에는 시험차수 2 이상이라는 추가 조건이 있다.
- 농도 10 mg/mL 이상이라는 조건은 함량시험의 희석 절차에만 쓰인다.
- 값 없음은 조건 미해당이 아니다. 해당 단계·배치에서 독립 사실을 찾지 못하면 보류한다.

문서/문단/구간 ID는 `prove_permit_applicability.py`가 만드는 고정 permit.pdf에 대한 검토 지문이다.
문서가 달라졌다고 ID를 자동 갱신해 적용하면 안 된다. 전체 문단과 조건의 의미를 다시 검토해야 한다.
이 설정은 코드에서 명시적으로 지정할 때만 읽으며 운영27개 MD에 자동 포함되지 않는다.
