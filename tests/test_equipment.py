from httpx import AsyncClient


async def test_create_equipment_returns_201_with_generated_id(client: AsyncClient, equipment_payload: dict) -> None:
    response = await client.post('/equipment', json=equipment_payload)

    assert response.status_code == 201
    body = response.json()
    assert body['id'] > 0
    assert body['tag'] == 'ANM-P-012'
    assert body['status'] == 'operating'


async def test_duplicate_tag_returns_409(client: AsyncClient, equipment_payload: dict) -> None:
    await client.post('/equipment', json=equipment_payload)

    response = await client.post('/equipment', json=equipment_payload)

    assert response.status_code == 409


async def test_malformed_tag_is_rejected_by_schema(client: AsyncClient, equipment_payload: dict) -> None:
    response = await client.post('/equipment', json={**equipment_payload, 'tag': 'not a tag'})

    assert response.status_code == 422


async def test_negative_depth_is_rejected_by_schema(client: AsyncClient, equipment_payload: dict) -> None:
    response = await client.post('/equipment', json={**equipment_payload, 'water_depth_m': -10})

    assert response.status_code == 422


async def test_get_unknown_equipment_returns_404(client: AsyncClient) -> None:
    response = await client.get('/equipment/9999')

    assert response.status_code == 404


async def test_list_filters_by_field_name(client: AsyncClient, equipment_payload: dict) -> None:
    await client.post('/equipment', json=equipment_payload)
    await client.post('/equipment', json={**equipment_payload, 'tag': 'MAN-A-004', 'field_name': 'Buzios'})

    response = await client.get('/equipment', params={'field_name': 'Buzios'})

    assert response.status_code == 200
    assert [item['tag'] for item in response.json()] == ['MAN-A-004']


async def test_patch_only_touches_sent_fields(client: AsyncClient, equipment_id: int) -> None:
    response = await client.patch(f'/equipment/{equipment_id}', json={'status': 'maintenance'})

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'maintenance'
    assert body['field_name'] == 'Tupi'
    assert body['water_depth_m'] == 2140.0


async def test_delete_removes_equipment(client: AsyncClient, equipment_id: int) -> None:
    assert (await client.delete(f'/equipment/{equipment_id}')).status_code == 204

    assert (await client.get(f'/equipment/{equipment_id}')).status_code == 404


async def test_upsert_limit_replaces_existing_bounds(client: AsyncClient, equipment_id: int) -> None:
    first = await client.put(
        f'/equipment/{equipment_id}/limits',
        json={'sensor_type': 'pressure', 'min_value': 100.0, 'max_value': 300.0},
    )
    second = await client.put(
        f'/equipment/{equipment_id}/limits',
        json={'sensor_type': 'pressure', 'min_value': 120.0, 'max_value': 280.0},
    )

    assert first.json()['id'] == second.json()['id']
    assert second.json()['max_value'] == 280.0

    listed = await client.get(f'/equipment/{equipment_id}/limits')
    assert len(listed.json()) == 1


async def test_limit_with_inverted_bounds_is_rejected(client: AsyncClient, equipment_id: int) -> None:
    response = await client.put(
        f'/equipment/{equipment_id}/limits',
        json={'sensor_type': 'pressure', 'min_value': 300.0, 'max_value': 100.0},
    )

    assert response.status_code == 422
