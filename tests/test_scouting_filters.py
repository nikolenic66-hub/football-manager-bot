from app.game.scouting import build_scout_count_query, build_scout_query


def test_scout_query_uses_all_filters_and_stable_order():
    query=build_scout_query({
        'position':['ST','LW'], 'age':(18,23), 'potential':{'min':80},
        'value':{'min':100_000,'max':5_000_000}, 'wage':{'max':100_000},
        'nationality':'SWE', 'transfer_status':'listed', 'preferred_foot':'L',
        'attributes':{'pace':{'min':75},'shooting':{'min':80}},
    })
    sql=str(query.compile(compile_kwargs={'literal_binds':True}))
    assert 'ORDER BY players.id ASC' in sql
    for fragment in ('players.age >= 18','players.age <= 23','players.potential >= 80','players.market_value >= 100000','players.salary <= 100000','players.nationality =','players.preferred_foot =','players.pace >= 75','players.shooting >= 80'):
        assert fragment in sql


def test_page_offsets_are_deterministic():
    query=build_scout_query({'age':(18,23)})
    page1=str(query.limit(8).offset(0).compile(compile_kwargs={'literal_binds':True}))
    page2=str(query.limit(8).offset(8).compile(compile_kwargs={'literal_binds':True}))
    page1_again=str(query.limit(8).offset(0).compile(compile_kwargs={'literal_binds':True}))
    assert page1==page1_again
    assert 'OFFSET 8' in page2


def test_count_query_matches_filter_shape():
    sql=str(build_scout_count_query({'age':(18,23),'potential':{'min':80}}).compile(compile_kwargs={'literal_binds':True}))
    assert 'count(*)' in sql
    assert 'players.age >= 18' in sql
    assert 'players.potential >= 80' in sql
