"""Train a small provisional Ridge regression without requiring scikit-learn."""
import json
from pathlib import Path


def train_pilot(matrix_path, targets_path, output_path, alpha=1.0):
    import numpy as np
    matrix=json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    targets=json.loads(Path(targets_path).read_text(encoding="utf-8"))
    target_map={row["company"]:row["target_score"] for row in targets["targets"]}
    rows=[row for row in matrix["rows"] if row["company"] in target_map]
    names=matrix["feature_names"]
    if len(rows)<3: raise ValueError("Pilot regression needs at least three companies.")
    X=np.array([[value if value is not None else 0.0 for value in row["feature_vector"]] for row in rows],dtype=float)
    y=np.array([target_map[row["company"]] for row in rows],dtype=float)
    X1=np.column_stack([np.ones(len(X)),X])
    penalty=np.eye(X1.shape[1]); penalty[0,0]=0
    beta=np.linalg.solve(X1.T@X1+alpha*penalty,X1.T@y)
    predictions=[]
    for row,features in zip(rows,X):
        value=float(np.clip(np.r_[1.0,features]@beta,0,100))
        predictions.append({"company":row["company"],"target_score":target_map[row["company"]],"predicted_score":round(value,1)})
    result={"industry":matrix["industry"],"fiscal_year":matrix["fiscal_year"],"feature_names":names,"coefficients":beta[1:].tolist(),"intercept":float(beta[0]),"predictions":predictions,"training_rows":len(rows),"provisional":True}
    Path(output_path).write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result
