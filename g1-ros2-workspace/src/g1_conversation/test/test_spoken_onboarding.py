from g1_conversation.onboarding import PassengerNameStep


def test_live_spoken_name_is_asked_then_remembered_only_in_this_session():
    step=PassengerNameStep()
    assert 'What is your name?' in step.greeting()
    assert 'Rafi' in step.reply('My name is Rafi.')
    assert step.name=='Rafi' and not step.pending
    assert step.reply('Take me to Office') is None
    other=PassengerNameStep()
    assert other.name=='' and other.pending


def test_name_prompt_does_not_consume_destinations_controls_or_acknowledgements():
    for text in ('Take me to Office','Where is Office?','Stop','Office'):
        step=PassengerNameStep()
        assert step.reply(text,[{'text':'Office'}]) is None
        assert step.name=='' and not step.pending
    step=PassengerNameStep()
    assert 'What is your name?' in step.reply('Okay') and step.pending
    assert 'Where would you like to go?' in step.reply('skip')
    assert step.name=='' and not step.pending
    known=PassengerNameStep(name='Rafi')
    assert 'Rafi' in known.greeting() and not known.pending
