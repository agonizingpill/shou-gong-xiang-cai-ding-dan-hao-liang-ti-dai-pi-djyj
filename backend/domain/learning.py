from hashlib import sha256
import json
from math import ceil
from functools import lru_cache
from random import Random

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

def historic():
    rng = Random(1095)
    for _ in range(360):
        grams = rng.randint(80,1200)
        moisture = rng.uniform(4,20)
        rack,scent = rng.randint(0,1),rng.randint(0,1)
        loss = grams*(0.03+.011*moisture+.012*rack+.018*scent)
        loss += max(0.0,moisture-13)*2.2+rng.uniform(-4,4)
        yield grams,moisture,rack,scent,max(0.0,loss)


@lru_cache(maxsize=1)
def build_model():
    records = np.asarray(list(historic()))
    train,check = records[:280],records[280:]
    model = HistGradientBoostingRegressor(max_iter=120,max_bins=48,max_leaf_nodes=15,
        min_samples_leaf=9,l2_regularization=0.1,early_stopping=False,random_state=1095).fit(train[:,:4],train[:,4])
    return model, records, train, check


def model_result(model, records, check, data):
    estimated = float(model.predict([[data['target'],data['moisture'],int(data['dryer']=='rack-b'),int(data['scent']=='PAPER')]])[0])
    loss = round(max(0.0,estimated),3)
    return {'algorithm':'HistGradientBoostingRegressor','training':280,'holdout':len(check),
            'trees':int(model.n_iter_),'seed':1095,'loss_g':loss,'need_g':data['target']+ceil(max(0.0,estimated)),
            'manual_baseline_mae_g':round(float(mean_absolute_error(check[:,4],.15*check[:,0])),3),
            'holdout_mae_g':round(float(mean_absolute_error(check[:,4],model.predict(check[:,:4]))),3),
            'input_hash':sha256(json.dumps([data,records.tolist()],sort_keys=True).encode()).hexdigest()}


def calculate(data):
    model,records,_train,check = build_model()
    return model_result(model,records,check,data)


def calculate_racks(base):
    """Fit once and return the wet-material estimate for both rack IDs."""
    model,records,_train,check = build_model()
    results = {}
    for dryer in ('rack-a','rack-b'):
        data = dict(base) | {'dryer': dryer}
        results[dryer] = model_result(model,records,check,data)
    return results
