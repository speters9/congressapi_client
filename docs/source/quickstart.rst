Quickstart
==========

Install and set your API key:

.. code-block:: bash

   pip install git+https://github.com/speters9/congressapi-client.git@main
   export CONGRESS_API_KEY="your-key-here"

Basic usage:

.. code-block:: python

   from congressapi_client import CongressAPIClient

   client = CongressAPIClient()

   bill = client.get_bill(117, "hr", 3076)
   print(bill.title)

   member = client.get_member("Y000064")
   print(member.full_name)

See the project `README <https://github.com/speters9/congressapi-client>`_ for a
description of rate limiting, retry/backoff behavior, and streaming with
``iter_entities()``.
