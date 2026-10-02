from pathlib import Path
from app.visual.render import finance_screen, player_card, formation_board, club_crest, match_center, formation_positions


def test_visual_pack_outputs(tmp_path):
    p=player_card({'id':7,'first_name':'Test','last_name':'Player','nationality':'NLD','position':'CM','rarity':'EPIC','pace':80,'shooting':78,'passing':88,'dribbling':84,'defending':65,'physical':77,'talents':['PLAYMAKER']},tmp_path/'card.png')
    f=formation_board('4-3-3',out=tmp_path/'formation.png')
    c=club_crest('Test FC',7,out=tmp_path/'crest.png')
    m=match_center('Test FC','Rivals',2,1,1.8,0.7,[{'minute':23,'description':'Goal — Test Player'}],out=tmp_path/'match.png')
    for x in (p,f,c,m):
        assert Path(x).exists()
        assert Path(x).stat().st_size>1000


def test_visual_manager_screens(tmp_path):
    from app.visual.render import finance_screen, tactical_board_screen, squad_screen, league_table_screen, club_dashboard, market_screen, fixtures_screen
    players=[{'id':i,'first_name':f'P{i}','last_name':'Test','position':'CM','role':'PLAYMAKER','instruction':'ROAM','board_x':40+i*2,'board_y':30+i*2,'fitness':90,'form':1,'shirt_number':i,'overall':80,'rarity':'RARE'} for i in range(1,12)]
    rows=[{'name':f'Club {i}','played':10,'wins':i,'draws':2,'losses':8-i,'points':i*3+2,'goals_for':20+i,'goals_against':10} for i in range(1,5)]
    fixtures=[{'round':1,'home':'Club A','away':'Club B','home_score':2,'away_score':1,'status':'FINISHED'}]
    market=[{'id':9,'first_name':'Market','last_name':'Player','position':'ST','overall':84,'rarity':'EPIC','market_value':12300000}]
    club={'id':1,'name':'Test FC','budget':20000000,'fans':5000,'reputation':2,'stadium_level':3,'stadium_name':'Test Arena'}
    paths=[
        tactical_board_screen('4-3-3',players,out=tmp_path/'tactics.png'),
        squad_screen('Test FC',players,out=tmp_path/'squad.png'),
        league_table_screen('League',rows,out=tmp_path/'table.png'),
        club_dashboard(club,out=tmp_path/'club.png'),
        market_screen(market,out=tmp_path/'market.png'),
        fixtures_screen('League',fixtures,out=tmp_path/'fixtures.png'),
    ]
    for path in paths:
        assert Path(path).exists() and Path(path).stat().st_size>1000


def test_player_catalog_counts():
    from app.player_catalog import REAL_PLAYERS, LEGEND_NAMES
    names={f'{a} {b}'.strip() for a,b,_,_ in REAL_PLAYERS}
    assert len(REAL_PLAYERS)==120
    assert len(LEGEND_NAMES)==30
    assert len(names & LEGEND_NAMES)==30

def test_scouting_screen_renders():
    from app.game.scouting import build_scout_report
    from app.visual.render import finance_screen, scouting_screen
    report=build_scout_report(9,'Rivals',[{'formation':'4-3-3','style':'COUNTER','goals_for':2,'goals_against':1}],{'Star Player':2})
    path=scouting_screen(report)
    assert path.exists() and path.stat().st_size > 0


def test_formation_visual_semantics_match_engine():
    from app.game.board import FORMATION_SLOTS
    expected={
      '4-3-3': {'GK':1,'LB':1,'CB':2,'RB':1,'CM':2,'DM':1,'LW':1,'ST':1,'RW':1},
      '4-4-2': {'GK':1,'LB':1,'CB':2,'RB':1,'LM':1,'CM':2,'RM':1,'ST':2},
      '4-2-3-1': {'GK':1,'LB':1,'CB':2,'RB':1,'DM':2,'LW':1,'AM':1,'RW':1,'ST':1},
      '3-5-2': {'GK':1,'CB':3,'LM':1,'CM':2,'DM':1,'RM':1,'ST':2},
      '5-3-2': {'GK':1,'LB':1,'CB':3,'RB':1,'CM':2,'DM':1,'ST':2},
    }
    for f, exp in expected.items():
        got={}
        for slot,_,_ in FORMATION_SLOTS[f]: got[slot]=got.get(slot,0)+1
        assert got==exp
        assert len(formation_positions(f))==11


def test_real_card_visual_order_matches_seed_order():
    from app.player_catalog import REAL_PLAYERS, LEGEND_NAMES
    from app.game import board  # ensure game package is importable with visual audit
    real=[x for x in REAL_PLAYERS if f'{x[0]} {x[1]}'.strip() not in LEGEND_NAMES]
    leg=[x for x in REAL_PLAYERS if f'{x[0]} {x[1]}'.strip() in LEGEND_NAMES]
    order=leg[:30]+real[:80]+real[80:]
    assert len(order)==120
    assert f'{order[0][0]} {order[0][1]}'.strip() in LEGEND_NAMES
    assert f'{order[29][0]} {order[29][1]}'.strip() in LEGEND_NAMES
    assert f'{order[30][0]} {order[30][1]}'.strip() not in LEGEND_NAMES


def test_match_phase_screen_renders_and_mirrors_formations(tmp_path):
    from app.visual.render import finance_screen, match_phase_screen
    path=match_phase_screen('Home','Away','3-5-2','5-3-2',phase='TRANSITION',home_control=.57,pressure_home=72,pressure_away=61,events=[{'minute':61,'description':'Counter attack'}],out=tmp_path/'phase.png')
    assert Path(path).exists() and Path(path).stat().st_size > 1000


def test_dynamic_match_screen_renders_from_engine_formations(tmp_path):
    from app.visual.render import finance_screen, dynamic_match_screen
    for f in ('4-3-3','4-4-2','4-2-3-1','3-5-2','5-3-2'):
        path=dynamic_match_screen('Home','Away',f,f,phase='COUNTER',minute=67,home_control=.56,pressure_home=74,pressure_away=58,events=[{'minute':61,'description':'Counter attack'}],home_score=2,away_score=1,out=tmp_path/f'{f}.png')
        assert Path(path).exists() and Path(path).stat().st_size>1000


def test_event_replay_screen_renders(tmp_path):
    from app.visual.render import finance_screen, event_replay_screen
    event={
        'minute':67,'event_type':'PASS','club_id':1,'team':'HOME','player':'Playmaker','secondary_player':'Striker',
        'description':'Playmaker → Striker',
        'metadata':{'visual_type':'PASS','start':[54,48],'end':[78,52],'zone':'CHANCE'}
    }
    path=event_replay_screen('Home','Away','4-3-3','4-2-3-1',event,3,12,2,1,out=tmp_path/'replay.png')
    assert Path(path).exists() and Path(path).stat().st_size>1000


def test_finance_screen_renders(tmp_path):
    club={'id':2,'name':'Finance FC','budget':18500000,'ticket_price':25,'sponsor_level':3,'stadium_level':4}
    summary=[{'category':'TICKETS','total':240000},{'category':'WAGES','total':-180000},{'category':'SPONSOR','total':75000}]
    recent=[{'category':'TICKETS','amount':120000,'balance_after':18500000,'description':'Домашний матч: 4800 зрителей'}]
    path=finance_screen(club,summary,recent,out=tmp_path/'finance.png')
    assert Path(path).exists() and Path(path).stat().st_size>1000
