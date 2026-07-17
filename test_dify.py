from dify_client import run_dify_workflow


sample_contract = {
    "contract_id": "CON-004",
    "contract_value": 1_200_000_000,
    "gross_margin": 24,
    "status": "pending"
}

result = run_dify_workflow(
    contract_id="CON-004",
    case_data=sample_contract
)

print(result)