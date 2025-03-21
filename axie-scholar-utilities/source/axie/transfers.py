import sys
import logging
import json
from datetime import datetime, timedelta
from time import sleep

from jsonschema import validate
from jsonschema.exceptions import ValidationError
from web3 import Web3, exceptions

from axie.schemas import transfers_schema
from axie.axies import Axies
from axie.utils import get_nonce, load_json, ImportantLogsFilter, RONIN_PROVIDER_FREE, AXIE_CONTRACT, TIMEOUT_MINS


now = int(datetime.now().timestamp())
log_file = f'logs/transfer_results_{now}.log'
logger = logging.getLogger()
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(log_file, mode='w')
file_handler.setLevel(logging.INFO)
file_handler.addFilter(ImportantLogsFilter())
logger.addHandler(file_handler)


class Transfer:
    def __init__(self, from_acc, from_private, to_acc, axie_id):
        self.w3 = Web3(Web3.HTTPProvider(RONIN_PROVIDER_FREE))
        self.from_acc = from_acc.replace("ronin:", "0x")
        self.from_private = from_private
        self.to_acc = to_acc.replace("ronin:", "0x")
        self.axie_id = axie_id

    def execute(self):
        # Load ABI
        with open('axie/axie_abi.json', encoding='utf-8') as f:
            axie_abi = json.load(f)
        axie_contract = self.w3.eth.contract(
            address=Web3.toChecksumAddress(AXIE_CONTRACT),
            abi=axie_abi
        )
        # Get Nonce
        nonce = get_nonce(self.from_acc)
        # Build transaction
        transaction = axie_contract.functions.safeTransferFrom(
            Web3.toChecksumAddress(self.from_acc),
            Web3.toChecksumAddress(self.to_acc),
            self.axie_id
        ).buildTransaction({
            "chainId": 2020,
            "gas": 500000,
            "from": Web3.toChecksumAddress(self.from_acc),
            "gasPrice": self.w3.toWei("0", "gwei"),
            "value": 0,
            "nonce": nonce
        })
        # Sign Transaction
        signed = self.w3.eth.account.sign_transaction(
            transaction,
            private_key=self.from_private
        )
        # Send raw transaction
        self.w3.eth.send_raw_transaction(signed.rawTransaction)
        # get transaction hash
        hash = self.w3.toHex(self.w3.keccak(signed.rawTransaction))
        # Wait for transaction to finish or timeout
        start_time = datetime.now()
        while True:
            # We will wait for max 10min for this trasnfer to happen
            if datetime.now() - start_time > timedelta(minutes=TIMEOUT_MINS):
                success = False
                logging.info(f"Important: Transfer {self}, timed out!")
                break
            try:
                recepit = self.w3.eth.get_transaction_receipt(hash)
                if recepit["status"] == 1:
                    success = True
                else:
                    success = False
                break
            except exceptions.TransactionNotFound:
                logging.info(f"Waiting for transfer '{self}' to finish (Nonce:{nonce})...")
                # Sleep 10 seconds not to constantly send requests!
                sleep(10)
        if success:
            logging.info(f"Important: {self} completed! Hash: {hash} - "
                         f"Explorer: https://explorer.roninchain.com/tx/{str(hash)}")
        else:
            logging.info(f"Important: {self} failed")

    def __str__(self):
        return (f"Axie Transfer of axie ({self.axie_id}) from account ({self.from_acc.replace('0x', 'ronin:')}) "
                f"to account ({self.to_acc.replace('0x', 'ronin:')})")


class AxieTransferManager:
    def __init__(self, transfers_file, secrets_file, secure=None):
        self.transfers_file = load_json(transfers_file)
        self.secrets_file = load_json(secrets_file)
        self.secure = secure

    def verify_inputs(self):
        logging.info("Validating file inputs...")
        validation_success = True
        # Validate transfers file
        try:
            validate(self.transfers_file, transfers_schema)
        except ValidationError as ex:
            logging.critical("Transfers file failed validation. Please review it. "
                             f"Error given: {ex.message}. "
                             f"For attribute in: {list(ex.path)}")
            validation_success = False
        # Check we have private keys for all accounts
        for acc in self.transfers_file:
            if acc["AccountAddress"] not in self.secrets_file:
                logging.critical(f"Account '{acc['AccountAddress']}' is not present in secret file, please add it.")
                validation_success = False
        for sf in self.secrets_file:
            if len(self.secrets_file[sf]) != 66 or self.secrets_file[sf][:2] != "0x":
                logging.critical(f"Private key for account {sf} is not valid, please review it!")
                validation_success = False
        if not validation_success:
            logging.critical("Please make sure your transfers.json file looks like the one in the README.md\n"
                             "Find it here: https://ferranmarin.github.io/axie-scholar-utilities/")
            logging.critical("If your problem is with secrets.json, "
                             "delete it and re-generate the file starting with an empty secrets file.")
            sys.exit()
        logging.info("Files correctly validated!")

    def prepare_transfers(self):
        transfers = []
        logging.info("Preparing transfers")
        for acc in self.transfers_file:
            axies_in_acc = Axies(acc['AccountAddress']).get_axies()
            for axie in acc['Transfers']:
                if not self.secure or (self.secure and axie['ReceiverAddress'] in self.secrets_file):
                    # Check axie in account
                    if axie['AxieId'] in axies_in_acc:
                        t = Transfer(
                            to_acc=axie['ReceiverAddress'],
                            from_private=self.secrets_file[acc['AccountAddress']],
                            from_acc=acc['AccountAddress'],
                            axie_id=axie['AxieId']
                        )
                        transfers.append(t)
                    else:
                        logging.info(f"Axie ({axie['AxieId']}) not in account ({acc['AccountAddress']}), skipping.")
        self.execute_transfers(transfers)

    def execute_transfers(self, transfers):
        logging.info("Starting to transfer axies")
        for t in transfers:
            t.execute()
        logging.info("Axie Transfers Finished")
