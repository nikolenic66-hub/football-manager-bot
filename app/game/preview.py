"""Iteration 6: pre-match preview and post-match analysis helpers.

This module is read-only with respect to the match engine: it reuses the
existing strength, xG and talent calculations and does not change engine
state or the database schema.
"""

from app.game.engine import _engine_team_strength, expected_goals, clamp_xg
from app.game.ratings import player_rating
from app.game.talents import TALENTS, _virtual_talents, talent_effects


def _name(player):
    if isinstance(player, dict):
        first=player.get('first_name','')
        last=player.get('last_name','')
    else:
        first=getattr(player,'first_name','')
        last=getattr(player,'last_name','')
    return ' '.join(str(x).strip() for x in (first,last) if str(x or '').strip()) or 'Player'


def _talent_ids(player):
    ids=list(_virtual_talents(player))
    for talent_id in getattr(player,'talents',()) or ():
        if talent_id in TALENTS and talent_id not in ids:
            ids.append(talent_id)
    return ids


def _phase_percent(home_value, away_value):
    if home_value == 0 and away_value == 0:
        return 'равно', 0.0
    if away_value == 0:
        return 'сильнее', 100.0
    pct=(home_value/away_value-1.0)*100.0
    if abs(pct)<0.5:
        return 'равно', 0.0
    return ('сильнее' if pct>0 else 'слабее'), round(abs(pct),1)


def _phase_dict(home_strength, away_strength):
    result={}
    for phase,attr in (('att','attack'),('mid','midfield'),('def','defense')):
        hv=float(getattr(home_strength,attr)); av=float(getattr(away_strength,attr))
        relation,pct=_phase_percent(hv,av)
        result[phase]={'home':round(hv,2),'away':round(av,2),'relation':relation,'percent':pct}
    return result


def _active_talents(players, tactics, opponent_tactics, context=None):
    context=dict(context or {})
    talent_counts={talent_id:0 for talent_id in TALENTS}
    sets=[]
    for player in players:
        ids=set(_talent_ids(player)); sets.append(ids)
        for talent_id in ids:
            talent_counts[talent_id]=talent_counts.get(talent_id,0)+1
    context['_talent_counts']=talent_counts
    active={}
    for player,ids in zip(players,sets):
        for talent_id in ids:
            spec=TALENTS[talent_id]
            one=talent_effects(player,tactics,opponent_tactics,{**context,'_only_talent':talent_id})
            phase=('att','mid','def')[one.index(max(one))] if max(one)>1 else spec['phase']
            if abs(one[{'att':0,'mid':1,'def':2}[spec['phase']]]-1.0)>1e-9:
                active.setdefault(talent_id,{'name':spec['name'],'phase':spec['phase'],'effect':float(spec['effect']),'count':0})
                active[talent_id]['count']+=1
    return sorted(active.values(),key=lambda x:(-x['effect'],-x['count'],x['name']))


def _style_matchup(home_tactics, away_tactics):
    hs=getattr(home_tactics.style,'value',home_tactics.style)
    aws=getattr(away_tactics.style,'value',away_tactics.style)
    labels={
        ('ATTACK','DEFENSE'):'ATTACK vs DEFENSE — атака упирается в плотную оборону',
        ('DEFENSE','ATTACK'):'DEFENSE vs ATTACK — оборона испытывает давление',
        ('ATTACK','POSSESSION'):'ATTACK vs POSSESSION — открытая борьба за темп',
        ('POSSESSION','ATTACK'):'POSSESSION vs ATTACK — борьба за контроль и переходы',
        ('COUNTER','POSSESSION'):'COUNTER vs POSSESSION — контратаки получают пространство',
        ('POSSESSION','COUNTER'):'POSSESSION vs COUNTER — осторожнее с потерями',
    }
    text=labels.get((hs,aws),f'{hs} vs {aws}')
    return {'home':hs,'away':aws,'text':text}


def build_preview(home_players, away_players, home_tactics, away_tactics, home_board, away_board):
    home_strength=_engine_team_strength(home_players,home_tactics.formation,home_board,home_tactics,away_tactics,{'minute':0,'home_score':0,'away_score':0,'score_diff':0,'side':'home'},'home')
    away_strength=_engine_team_strength(away_players,away_tactics.formation,away_board,away_tactics,home_tactics,{'minute':0,'home_score':0,'away_score':0,'score_diff':0,'side':'away'},'away')
    hx,ax=expected_goals(home_players,away_players,home_tactics,away_tactics,home_board,away_board)
    home_top=sorted(home_players,key=player_rating,reverse=True)[:3]
    away_top=sorted(away_players,key=player_rating,reverse=True)[:3]
    context={'minute':0,'home_score':0,'away_score':0,'score_diff':0}
    return {
        'phases':_phase_dict(home_strength,away_strength),
        'key_players':{
            'home':[{'name':_name(p),'position':getattr(p,'position','—'),'ovr':round(player_rating(p),1)} for p in home_top],
            'away':[{'name':_name(p),'position':getattr(p,'position','—'),'ovr':round(player_rating(p),1)} for p in away_top],
        },
        'active_talents':{
            'home':_active_talents(home_players,home_tactics,away_tactics,context),
            'away':_active_talents(away_players,away_tactics,home_tactics,context),
        },
        'style_matchup':_style_matchup(home_tactics,away_tactics),
        'xg_range':{'home':(round(clamp_xg(hx*.85),2),round(clamp_xg(hx*1.15),2)),
                    'away':(round(clamp_xg(ax*.85),2),round(clamp_xg(ax*1.15),2))},
    }


def _urgency_text(score_diff, minute=90):
    if score_diff<0 and minute>60:
        return '+5% att / -3% def'
    if score_diff>0 and minute>75:
        return '-2% att / +3% def'
    if score_diff==0 and minute>80:
        return '+2% att'
    return 'не активировалась'


def _fatigue_estimate(tactics, minutes=90):
    return min(100.0,(0.25+0.0025*float(tactics.pressing)+0.0015*float(tactics.tempo))*minutes)


def build_post_match_analysis(home_state, away_state, match_events, modifiers_log=None):
    """Build an explainable post-match summary.

    The current engine does not persist a per-minute modifiers log. When it is
    absent, fatigue and urgency are reconstructed from final tactics/score and
    event timing rather than inventing a historical modifier trace.
    """
    hs=home_state; aws=away_state
    home_players=hs.get('players',[]); away_players=aws.get('players',[])
    ht=hs['tactics']; at=aws['tactics']
    home_board=hs.get('board'); away_board=aws.get('board')
    home_score=int(hs.get('score',0)); away_score=int(aws.get('score',0))
    minute=int(hs.get('minute',90) or 90)
    hctx={'minute':minute,'home_score':home_score,'away_score':away_score,'score_diff':home_score-away_score,'side':'home'}
    actx={**hctx,'score_diff':away_score-home_score,'side':'away'}
    h=_engine_team_strength(home_players,ht.formation,home_board,ht,at,hctx,'home')
    a=_engine_team_strength(away_players,at.formation,away_board,at,ht,actx,'away')
    phases=_phase_dict(h,a)
    goal_events=[e for e in match_events if str(e.get('event_type',e.get('type',''))).upper()=='GOAL']
    active=_active_talents(home_players,ht,at,hctx)+_active_talents(away_players,at,ht,actx)
    active.sort(key=lambda x:(-x['effect'],-x['count']))
    top_talents=[{'name':x['name'],'count':x['count'],'effect':round(x['effect']*100,1),'phase':x['phase']} for x in active[:3]]
    return {
        'phase_breakdown':phases,
        'top_talents':top_talents,
        'fatigue_impact':{
            'home_percent':round(_fatigue_estimate(ht,minute),1),
            'away_percent':round(_fatigue_estimate(at,minute),1),
            'note':'оценка по темпу/прессингу; детального modifiers_log в текущем движке нет',
        },
        'urgency_impact':{
            'home':_urgency_text(home_score-away_score,minute),
            'away':_urgency_text(away_score-home_score,minute),
            'goals':len(goal_events),
        },
    }
